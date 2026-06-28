import os
import json
import re
import base64
import asyncio
from dotenv import load_dotenv
from playwright.async_api import async_playwright

from src.core import config
from src.services.ingest import ingest_download_dir

load_dotenv() 

HISTORY_FILE = os.path.join(config.DOWNLOAD_DIR, "downloaded_records.json")

crawler_state = {
    "status": "idle",
    "captcha_b64": None,
    "login_data": None,
    "message": ""
}

def load_history() -> set:
    if os.path.exists(HISTORY_FILE):
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            try: return set(json.load(f))
            except: return set()
    return set()

def save_history(history_set: set):
    with open(HISTORY_FILE, "w", encoding="utf-8") as f:
        json.dump(list(history_set), f, ensure_ascii=False, indent=4)

def sanitize_filename(name: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "_", name).strip()

async def run_spider(limit: int):
    global crawler_state
    
    crawler_state.update({
        "status": "starting",
        "login_data": None,
        "captcha_b64": None,
        "message": "Đang khởi động trình duyệt ảo (Playwright)..."
    })
    
    os.makedirs(config.DOWNLOAD_DIR, exist_ok=True)
    history_set = load_history()
    
    QLVB_URL = os.getenv("QLVB_URL", "https://egov1.laocai.gov.vn")
    TARGET_URL = f"{QLVB_URL}/document/xem-di-index?statustype=published&type=vanbandi"
    total_downloaded = 0

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=['--no-sandbox', '--disable-setuid-sandbox']
            )
            
            context = await browser.new_context(
                viewport={'width': 1280, 'height': 720}, 
                ignore_https_errors=True,                
                accept_downloads=True
            )
            page = await context.new_page()

            login_success = False
            
            await page.goto(QLVB_URL, wait_until="domcontentloaded")
            await page.wait_for_timeout(2000)

            # --- VÒNG LẶP ĐĂNG NHẬP ---
            for attempt in range(5):
                crawler_state["message"] = f"Đang chờ load trang đăng nhập (Lần thử {attempt+1}/5)..."
                
                await page.wait_for_selector("input#usernameUserInput", timeout=60000)

                captcha_element = await page.query_selector("img#captchaImage")
                if not captcha_element: continue

                captcha_bytes = await captcha_element.screenshot()
                crawler_state["captcha_b64"] = base64.b64encode(captcha_bytes).decode('utf-8')
                crawler_state["login_data"] = None 
                crawler_state["status"] = "waiting_login"
                crawler_state["message"] = "Vui lòng nhập thông tin trên cửa sổ web vừa bật lên..."

                wait_time = 0
                while crawler_state["login_data"] is None and wait_time < 300:
                    await asyncio.sleep(1)
                    wait_time += 1
                
                if crawler_state["login_data"] is None:
                    crawler_state["status"] = "error"
                    crawler_state["message"] = "Hết thời gian chờ nhập Captcha."
                    return

                data = crawler_state["login_data"]
                crawler_state["status"] = "logging_in"
                crawler_state["message"] = "Đang submit form đăng nhập..."

                await page.fill("input#usernameUserInput", data.get("username", ""))
                await page.fill("input#password", data.get("password", ""))
                await page.wait_for_timeout(1000)
                await page.fill("input#captcha", data.get("captcha", ""))
                await page.click("button[type='submit']")

                try:
                    await page.wait_for_url("**/home/default/1", timeout=15000)
                    login_success = True
                    break
                except Exception:
                    crawler_state["message"] = "Sai tài khoản/mật khẩu/Captcha. Đang tải lại ảnh mới..."
                    try:
                        await page.click("img#captchaImage", timeout=3000)
                        await page.wait_for_timeout(1500)
                    except: pass
            
            if not login_success:
                crawler_state["status"] = "error"
                crawler_state["message"] = "Đăng nhập thất bại quá 5 lần. Đã hủy."
                return

            # --- VÒNG LẶP CÀO DỮ LIỆU ---
            crawler_state["status"] = "crawling"
            crawler_state["message"] = "Đăng nhập thành công! Đang truy cập danh sách..."
            
            # Khắc phục lỗi Timeout 15s bằng cách Tăng lên 60s
            await page.goto(TARGET_URL, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_selector("table tbody tr", timeout=60000)
            
            # --- BÊ NGUYÊN CODE CŨ: Sắp xếp theo ngày ban hành ---
            crawler_state["message"] = "Đang thao tác sắp xếp văn bản theo 'Ngày ban hành'..."
            try:
                sort_icon = page.locator("table thead th").nth(5).locator("i").first
                await sort_icon.click()
                await page.wait_for_timeout(3000)
                
                await sort_icon.click()
                await page.wait_for_timeout(3000)
            except Exception:
                pass
            
            page_num = 1
            has_next_page = True
            
            while has_next_page:
                crawler_state["message"] = f"Đang quét trang {page_num}... (Đã tải {total_downloaded})"
                await page.wait_for_timeout(2000)
                
                rows = await page.locator("table tbody tr").all()
                new_docs_in_page = 0
                
                for row in rows:
                    if limit > 0 and total_downloaded >= limit: break
                    
                    cells = row.locator("td")
                    if await cells.count() < 6: continue
                    
                    trich_yeu = (await cells.nth(1).inner_text()).strip()
                    so_ky_hieu = (await cells.nth(3).inner_text()).strip()
                    ngay_ban_hanh = (await cells.nth(5).inner_text()).strip()
                    
                    if not so_ky_hieu: continue
                    if so_ky_hieu in history_set: continue
                    
                    new_docs_in_page += 1
                    total_downloaded += 1
                    crawler_state["message"] = f"Đang tải {total_downloaded}/{limit if limit > 0 else 'Tất cả'}: {so_ky_hieu}"

                    # =======================================================
                    # XỬ LÝ HỘP THOẠI (MODAL) TẢI FILE
                    # =======================================================
                    try:
                        # 1. Tìm và click vào button ở cột cuối cùng
                        button_locator = cells.last.locator("button")
                        if await button_locator.count() > 0:
                            await button_locator.first.click()
                        else:
                            await cells.last.click()

                        # 2. Đợi hộp thoại ngb-modal-window bật lên
                        modal = page.locator("ngb-modal-window")
                        await modal.wait_for(state="visible", timeout=15000)
                        await page.wait_for_timeout(1000)

                        # 3. Lấy tất cả các thẻ link <a> nằm trong hộp thoại
                        file_links = await modal.locator("a").all()
                        for link_idx, link in enumerate(file_links):
                            try:
                                async with page.expect_download(timeout=30000) as download_info:
                                    await link.click()
                                
                                download = await download_info.value
                                new_filename = f"{sanitize_filename(so_ky_hieu)}_{download.suggested_filename}"
                                file_path = os.path.join(config.DOWNLOAD_DIR, new_filename)
                                
                                await download.save_as(file_path)
                                
                                meta_path = file_path + ".meta.json"
                                with open(meta_path, "w", encoding="utf-8") as mf:
                                    json.dump({
                                        "so_ky_hieu": so_ky_hieu,
                                        "ngay_ban_hanh": ngay_ban_hanh,
                                        "trich_yeu": trich_yeu,
                                        "huong": "di",
                                        "file_goc": download.suggested_filename
                                    }, mf, ensure_ascii=False, indent=2)
                            except Exception:
                                pass

                        # 4. Tắt hộp thoại bằng cách nhấn phím ESC
                        await page.keyboard.press("Escape")
                        await modal.wait_for(state="hidden", timeout=5000)

                    except Exception:
                        await page.keyboard.press("Escape")
                    # =======================================================

                    history_set.add(so_ky_hieu)
                    save_history(history_set)

                if limit > 0 and total_downloaded >= limit: break
                if new_docs_in_page == 0 and page_num > 1: break

                next_li = page.locator("li.page-item", has=page.locator("a", has_text="›")).first
                class_attr = await next_li.get_attribute("class")
                if class_attr and "disabled" in class_attr:
                    has_next_page = False
                else:
                    await next_li.click()
                    await page.wait_for_timeout(3000) 
                    page_num += 1

            await browser.close()
            crawler_state["message"] = "Đã tải xong văn bản! Đang tiến hành nạp vào AI (Ingest)..."
            
            try:
                await asyncio.to_thread(ingest_download_dir)
                crawler_state["status"] = "done"
                crawler_state["message"] = "Hoàn tất! CSDL Vector và Postgres đã được cập nhật."
            except Exception as e:
                crawler_state["status"] = "error"
                crawler_state["message"] = f"Lỗi nạp AI Ingest: {str(e)}"

    except Exception as e:
        crawler_state["status"] = "error"
        crawler_state["message"] = f"Lỗi hệ thống: {str(e)}"