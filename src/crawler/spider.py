from __future__ import annotations

import asyncio
import base64
import json
import os
import re
import unicodedata
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from dotenv import load_dotenv
from playwright.async_api import async_playwright

from src.core import config
from src.crawler.history import CrawlHistory, source_key
from src.services.ingest import ingest_download_dir

load_dotenv()

crawler_state = {
    "status": "idle",
    "captcha_b64": None,
    "login_data": None,
    "message": "",
}

USERNAME_SELECTORS = (
    "input#usernameUserInput",
    "input[name='usernameUserInput']",
    "input[autocomplete='username']",
    "input[name='username']:not([type='hidden'])",
    "input[type='email']",
)
PASSWORD_SELECTORS = (
    "input#password",
    "input[name='password']",
    "input[type='password']",
)
CAPTCHA_INPUT_SELECTORS = (
    "input#captcha",
    "input[name='captcha']",
    "input[placeholder*='xác thực' i]",
    "input[placeholder*='captcha' i]",
)
CAPTCHA_IMAGE_SELECTORS = (
    "img#captchaImage",
    "img[id*='captcha' i]",
    "img[src*='captcha' i]",
)
SUBMIT_SELECTORS = (
    "button[type='submit']",
    "input[type='submit']",
    "button:has-text('Đăng nhập')",
    "button:has-text('Login')",
)


def get_history_file(direction: str) -> str:
    filename = "downloaded_records_di.json" if direction == "di" else "downloaded_records_den.json"
    return os.path.join(config.DOWNLOAD_DIR, filename)


def sanitize_filename(name: str) -> str:
    cleaned = re.sub(r'[\\/*?:"<>|]', "_", name or "").strip().strip(".")
    return cleaned[:180] or "khong_ten"


def _safe_url(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"


def _normalize_label(value: str) -> str:
    value = unicodedata.normalize("NFD", value or "")
    value = "".join(char for char in value if unicodedata.category(char) != "Mn")
    return re.sub(r"\s+", " ", value.lower()).strip()


async def _first_visible(frame, selectors: tuple[str, ...]):
    for selector in selectors:
        locator = frame.locator(selector).first
        try:
            if await locator.count() and await locator.is_visible():
                return locator
        except Exception:
            continue
    return None


async def _find_login_form(page) -> dict[str, Any] | None:
    for frame in page.frames:
        username = await _first_visible(frame, USERNAME_SELECTORS)
        password = await _first_visible(frame, PASSWORD_SELECTORS)
        captcha_input = await _first_visible(frame, CAPTCHA_INPUT_SELECTORS)
        captcha_image = await _first_visible(frame, CAPTCHA_IMAGE_SELECTORS)
        submit = await _first_visible(frame, SUBMIT_SELECTORS)
        if username and password and captcha_input and captcha_image and submit:
            return {
                "frame": frame,
                "username": username,
                "password": password,
                "captcha_input": captcha_input,
                "captcha_image": captcha_image,
                "submit": submit,
            }
    return None


async def _wait_for_login_form(page, timeout_ms: int) -> dict[str, Any] | None:
    deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
    while asyncio.get_running_loop().time() < deadline:
        form = await _find_login_form(page)
        if form:
            return form
        await asyncio.sleep(0.5)
    return None


async def _goto_with_retry(page, url: str, label: str) -> None:
    last_error: Exception | None = None
    for attempt in range(1, config.CRAWLER_NAVIGATION_RETRIES + 1):
        crawler_state["message"] = f"Đang mở {label} (lần {attempt}/{config.CRAWLER_NAVIGATION_RETRIES})..."
        try:
            response = await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=config.CRAWLER_PAGE_TIMEOUT_SECONDS * 1000,
            )
            if response and response.status >= 500:
                raise RuntimeError(f"HTTP {response.status}")
            return
        except Exception as exc:
            last_error = exc
            if attempt < config.CRAWLER_NAVIGATION_RETRIES:
                await asyncio.sleep(min(2 ** attempt, 10))
    raise RuntimeError(f"Không mở được {label}: {last_error}")


async def _capture_login_diagnostic(page) -> None:
    try:
        directory = Path(config.STORE_DIR) / "crawler_diagnostics"
        directory.mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(directory / "login-page.png"), full_page=True)
    except Exception:
        pass


async def _open_login_form(page, qlvb_url: str) -> dict[str, Any]:
    last_location = qlvb_url
    for attempt in range(1, config.CRAWLER_LOGIN_RETRIES + 1):
        try:
            await _goto_with_retry(page, qlvb_url, "cổng QLVB/SSO")
            last_location = page.url
            form = await _wait_for_login_form(
                page,
                config.CRAWLER_LOGIN_FORM_TIMEOUT_SECONDS * 1000,
            )
            if form:
                return form
        except Exception:
            last_location = page.url
        await _capture_login_diagnostic(page)
        crawler_state["message"] = (
            f"Chưa nhận được form SSO, đang thử lại ({attempt}/{config.CRAWLER_LOGIN_RETRIES})..."
        )
        await asyncio.sleep(min(2 ** attempt, 10))
    raise RuntimeError(
        "Không tìm thấy form đăng nhập SSO sau nhiều lần điều hướng. "
        f"Trang cuối: {_safe_url(last_location)}. Ảnh chẩn đoán đã được lưu."
    )


async def _wait_for_login_data() -> dict[str, str]:
    deadline = asyncio.get_running_loop().time() + config.CRAWLER_LOGIN_INPUT_TIMEOUT_SECONDS
    while asyncio.get_running_loop().time() < deadline:
        data = crawler_state.get("login_data")
        if data:
            crawler_state["login_data"] = None
            return data
        await asyncio.sleep(0.5)
    raise TimeoutError("Hết thời gian chờ nhập tài khoản và captcha.")


async def _wait_for_application(page, qlvb_url: str) -> bool:
    target_host = urlparse(qlvb_url).hostname
    deadline = asyncio.get_running_loop().time() + config.CRAWLER_LOGIN_RESULT_TIMEOUT_SECONDS
    while asyncio.get_running_loop().time() < deadline:
        parsed = urlparse(page.url)
        path = parsed.path.lower()
        if parsed.hostname == target_host and (
            path.startswith("/home/") or path.startswith("/document/")
        ):
            return True
        await asyncio.sleep(0.5)
    return False


async def login(page, qlvb_url: str) -> None:
    form = await _find_login_form(page) or await _open_login_form(page, qlvb_url)
    for attempt in range(1, config.CRAWLER_LOGIN_RETRIES + 1):
        if not form:
            form = await _open_login_form(page, qlvb_url)

        captcha_bytes = await form["captcha_image"].screenshot()
        crawler_state.update(
            {
                "captcha_b64": base64.b64encode(captcha_bytes).decode("utf-8"),
                "login_data": None,
                "status": "waiting_login",
                "message": f"Nhập tài khoản và captcha SSO (lần {attempt}/{config.CRAWLER_LOGIN_RETRIES}).",
            }
        )
        data = await _wait_for_login_data()
        crawler_state.update(
            {
                "status": "logging_in",
                "message": "Đang xác thực với cổng SSO...",
                "captcha_b64": None,
            }
        )

        await form["username"].fill(data.get("username", ""))
        await form["password"].fill(data.get("password", ""))
        await form["captcha_input"].fill(data.get("captcha", ""))
        data.clear()
        await form["submit"].click(timeout=15_000)

        if await _wait_for_application(page, qlvb_url):
            crawler_state["login_data"] = None
            crawler_state["captcha_b64"] = None
            return

        crawler_state["message"] = "Đăng nhập chưa thành công; đang làm mới captcha..."
        form = await _find_login_form(page)
        if form:
            try:
                await form["captcha_image"].click(timeout=5_000)
                await page.wait_for_timeout(1_500)
            except Exception:
                form = None

    raise RuntimeError("Đăng nhập SSO thất bại sau số lần thử cho phép.")


async def _close_modal(page, modal) -> None:
    try:
        close_button = modal.locator(
            "button[aria-label='Close'], button.close, .modal-header button, button:has-text('Đóng')"
        ).first
        if await close_button.count() and await close_button.is_visible():
            await close_button.click(timeout=5_000)
        else:
            await page.keyboard.press("Escape")
        await modal.wait_for(state="hidden", timeout=5_000)
    except Exception:
        try:
            await page.keyboard.press("Escape")
        except Exception:
            pass


async def handle_download_modal(
    page,
    cells,
    columns: dict[str, int],
    document_ref: str,
    issued_date: str,
    subject: str,
    direction: str,
    agency: str = "",
) -> int:
    modal = None
    downloads_saved = 0
    try:
        cell_count = await cells.count()
        attachment_index = columns.get("attachment")
        if attachment_index is None or attachment_index >= cell_count:
            if direction == "den":
                attachment_index = 15 if cell_count > 15 else max(0, cell_count - 2)
            else:
                attachment_index = cell_count - 1
        target_cell = cells.nth(max(0, attachment_index))
        clickable = target_cell.locator("a:visible, button:visible, [role='button']:visible, i:visible").first
        if await clickable.count():
            await clickable.click(timeout=15_000)
        else:
            await target_cell.click(timeout=15_000)

        modal = page.locator("ngb-modal-window:visible, .modal.show:visible, [role='dialog']:visible").first
        await modal.wait_for(state="visible", timeout=20_000)
        links = modal.locator("a:visible")
        link_count = await links.count()
        if link_count == 0:
            raise RuntimeError("Modal không có liên kết tệp đính kèm.")

        record_key = source_key(document_ref, issued_date, subject)
        for index in range(link_count):
            link = links.nth(index)
            download = None
            try:
                async with page.expect_download(timeout=config.CRAWLER_DOWNLOAD_TIMEOUT_SECONDS * 1000) as info:
                    await link.click(timeout=10_000)
                download = await info.value
                suggested = sanitize_filename(download.suggested_filename)
                prefix = sanitize_filename(document_ref or record_key[-16:])
                filename = f"{prefix}_{index + 1:02d}_{suggested}"
                file_path = os.path.join(config.DOWNLOAD_DIR, filename)
                await download.save_as(file_path)
                meta_path = file_path + ".meta.json"
                with open(meta_path, "w", encoding="utf-8") as stream:
                    json.dump(
                        {
                            "so_ky_hieu": document_ref,
                            "ngay_ban_hanh": issued_date,
                            "trich_yeu": subject,
                            "huong": direction,
                            "co_quan_ban_hanh": agency,
                            "file_goc": download.suggested_filename,
                            "source_url": page.url,
                            "history_key": record_key,
                        },
                        stream,
                        ensure_ascii=False,
                    )
                downloads_saved += 1
            except Exception as exc:
                print(f"  [!] Lỗi tải tệp {index + 1} của {document_ref}: {exc}")
            finally:
                if download is not None:
                    try:
                        await download.delete()
                    except Exception:
                        pass
        return downloads_saved
    except Exception as exc:
        print(f"  [!] Lỗi modal của {document_ref}: {exc}")
        return downloads_saved
    finally:
        if modal is not None:
            await _close_modal(page, modal)


def _find_column(headers: list[str], aliases: tuple[str, ...], fallback: int | None) -> int | None:
    normalized_aliases = tuple(_normalize_label(alias) for alias in aliases)
    for index, header in enumerate(headers):
        if any(alias in header for alias in normalized_aliases):
            return index
    return fallback


async def _column_map(page, direction: str) -> dict[str, int]:
    raw_headers = await page.locator("table thead th").all_inner_texts()
    headers = [_normalize_label(header) for header in raw_headers]
    defaults = {"subject": 1, "reference": 3, "date": 5} if direction == "di" else {
        "subject": 2,
        "reference": 3,
        "date": 4,
        "agency": 8,
    }
    columns = {
        "subject": _find_column(headers, ("trích yếu", "nội dung", "tên văn bản"), defaults["subject"]),
        "reference": _find_column(headers, ("số ký hiệu", "số, ký hiệu", "ký hiệu"), defaults["reference"]),
        "date": _find_column(headers, ("ngày ban hành", "ngày ký", "ngày văn bản"), defaults["date"]),
        "attachment": _find_column(headers, ("tệp", "file", "đính kèm", "tải"), None),
    }
    if direction == "den":
        columns["agency"] = _find_column(
            headers,
            ("cơ quan ban hành", "nơi gửi", "đơn vị gửi"),
            defaults["agency"],
        )
    return {key: value for key, value in columns.items() if value is not None}


async def _wait_for_table(page, qlvb_url: str, target_url: str) -> None:
    try:
        await page.locator("table tbody tr").first.wait_for(
            state="visible",
            timeout=config.CRAWLER_PAGE_TIMEOUT_SECONDS * 1000,
        )
    except Exception:
        if await _find_login_form(page):
            crawler_state["message"] = "Phiên SSO đã hết hạn, cần đăng nhập lại."
            await login(page, qlvb_url)
            await _goto_with_retry(page, target_url, "danh mục văn bản sau đăng nhập lại")
            await page.locator("table tbody tr").first.wait_for(
                state="visible",
                timeout=config.CRAWLER_PAGE_TIMEOUT_SECONDS * 1000,
            )
        else:
            raise


def _completed_history_records(result: dict) -> list[tuple[str, str, str]]:
    records: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for raw_meta in result.get("completed_documents", []):
        direction = raw_meta.get("huong", "di")
        document_ref = raw_meta.get("so_ky_hieu", "")
        key = raw_meta.get("history_key") or source_key(
            document_ref,
            raw_meta.get("ngay_ban_hanh", ""),
            raw_meta.get("trich_yeu", ""),
        )
        identity = (direction, key)
        if identity in seen:
            continue
        seen.add(identity)
        records.append((direction, key, document_ref))
    return records


async def _flush_batch(pending: list[tuple[str, str, str]], history: CrawlHistory) -> int:
    if not pending:
        return 0
    crawler_state["message"] = f"Đang nạp một lô {len(pending)} văn bản vào AI..."
    result = await asyncio.to_thread(ingest_download_dir, config.DOWNLOAD_DIR)
    completed = _completed_history_records(result)
    if completed:
        history.mark_completed(completed)
    failed_count = len(result.get("failed_files", []))
    if failed_count:
        crawler_state["message"] = (
            f"Đã bỏ qua và cách ly {failed_count} tệp lỗi; đang tiếp tục crawl."
        )
    pending.clear()
    return failed_count


async def _next_page(page, current_fingerprint: str) -> str:
    selectors = (
        "li.page-item a:has-text('›')",
        "li.page-item a[aria-label*='Next' i]",
        "a[aria-label*='Trang sau' i]",
        ".pagination-next:not(.disabled) a",
    )
    next_link = None
    for selector in selectors:
        candidate = page.locator(selector).first
        if await candidate.count() and await candidate.is_visible():
            parent_class = await candidate.locator("xpath=..").get_attribute("class") or ""
            own_class = await candidate.get_attribute("class") or ""
            if "disabled" not in f"{parent_class} {own_class}".lower():
                next_link = candidate
                break
    if next_link is None:
        return "end"

    await next_link.click(timeout=15_000)
    deadline = asyncio.get_running_loop().time() + config.CRAWLER_PAGE_TIMEOUT_SECONDS
    while asyncio.get_running_loop().time() < deadline:
        if await _find_login_form(page):
            return "stalled"
        rows = page.locator("table tbody tr")
        if await rows.count():
            fingerprint = (await rows.first.inner_text()).strip()
            if fingerprint and fingerprint != current_fingerprint:
                return "advanced"
        await asyncio.sleep(0.5)
    return "stalled"


async def crawl_table(
    page,
    qlvb_url: str,
    target_url: str,
    direction: str,
    limit: int,
    history: CrawlHistory,
    total_downloaded: int,
) -> int:
    crawler_state["message"] = f"Đang truy cập Văn bản {direction.upper()}..."
    await _goto_with_retry(page, target_url, f"danh mục Văn bản {direction.upper()}")
    await _wait_for_table(page, qlvb_url, target_url)

    try:
        sort_icon = page.locator("table thead th").nth(5).locator("i").first
        if await sort_icon.count():
            await sort_icon.click(timeout=5_000)
            await page.wait_for_timeout(1_500)
            await sort_icon.click(timeout=5_000)
            await page.wait_for_timeout(1_500)
    except Exception:
        pass

    page_number = 1
    seen_pages: set[str] = set()
    pending: list[tuple[str, str, str]] = []
    previously_complete = history.has_reached_end(direction)
    scan_had_failures = False
    history.mark_incomplete(direction)

    while True:
        rows_locator = page.locator("table tbody tr")
        await rows_locator.first.wait_for(
            state="visible",
            timeout=config.CRAWLER_PAGE_TIMEOUT_SECONDS * 1000,
        )
        rows = await rows_locator.all()
        if not rows:
            break
        fingerprint = (await rows[0].inner_text()).strip()
        if fingerprint in seen_pages:
            print(f"  [!] Dừng để tránh lặp trang {page_number} ({direction}).")
            break
        seen_pages.add(fingerprint)
        columns = await _column_map(page, direction)
        crawler_state["message"] = (
            f"[{direction.upper()}] Quét trang {page_number}; đã tải {total_downloaded}."
        )
        page_had_unseen = False

        for row in rows:
            if limit > 0 and total_downloaded >= limit:
                break
            cells = row.locator("td")
            cell_count = await cells.count()
            required = max(columns.get("subject", 0), columns.get("reference", 0), columns.get("date", 0))
            if cell_count <= required:
                continue

            subject = (await cells.nth(columns["subject"]).inner_text()).strip()
            document_ref = (await cells.nth(columns["reference"]).inner_text()).strip()
            issued_date = (await cells.nth(columns["date"]).inner_text()).strip()
            agency = ""
            if direction == "den" and columns.get("agency", cell_count) < cell_count:
                agency = (await cells.nth(columns["agency"]).inner_text()).strip()

            key = source_key(document_ref, issued_date, subject)
            if history.contains(direction, key):
                continue
            page_had_unseen = True
            crawler_state["message"] = (
                f"[{direction.upper()}] Đang tải {total_downloaded + 1}/"
                f"{limit if limit > 0 else 'tất cả'}: {document_ref or 'không số'}"
            )
            saved = await handle_download_modal(
                page,
                cells,
                columns,
                document_ref,
                issued_date,
                subject,
                direction,
                agency,
            )
            if saved == 0:
                scan_had_failures = True
                continue
            total_downloaded += 1
            pending.append((direction, key, document_ref))
            if len(pending) >= config.CRAWLER_BATCH_SIZE:
                if await _flush_batch(pending, history):
                    scan_had_failures = True

        if await _flush_batch(pending, history):
            scan_had_failures = True
        if limit > 0 and total_downloaded >= limit:
            break
        if config.CRAWLER_MAX_PAGES > 0 and page_number >= config.CRAWLER_MAX_PAGES:
            break
        if previously_complete and not page_had_unseen and not scan_had_failures:
            history.mark_reached_end(direction)
            break

        pagination = await _next_page(page, fingerprint)
        if pagination != "advanced":
            if await _find_login_form(page):
                crawler_state["message"] = "Phiên SSO hết hạn giữa quá trình; đang đăng nhập lại..."
                await login(page, qlvb_url)
                await _goto_with_retry(page, target_url, "danh mục sau khi gia hạn SSO")
                await _wait_for_table(page, qlvb_url, target_url)
                page_number = 1
                seen_pages.clear()
                continue
            if pagination == "end":
                if not scan_had_failures:
                    history.mark_reached_end(direction)
                break
            raise RuntimeError(
                f"Không thể chuyển sang trang {page_number + 1} của Văn bản {direction.upper()}."
            )
        page_number += 1

    await _flush_batch(pending, history)
    return total_downloaded


async def _ingest_leftovers(history: CrawlHistory) -> None:
    try:
        has_metadata = any(
            name.endswith(".meta.json") for name in os.listdir(config.DOWNLOAD_DIR)
        )
    except FileNotFoundError:
        return
    if not has_metadata:
        return

    crawler_state["message"] = "Đang xử lý lô tải dở từ lần chạy trước..."
    result = await asyncio.to_thread(ingest_download_dir, config.DOWNLOAD_DIR)
    completed = _completed_history_records(result)
    if completed:
        history.mark_completed(completed)
    failed_count = len(result.get("failed_files", []))
    if failed_count:
        crawler_state["message"] = (
            f"Đã cách ly {failed_count} tệp tải dở bị lỗi; tiếp tục khởi động crawler."
        )


async def run_spider(limit: int, mode: str = "all"):
    crawler_state.update(
        {
            "status": "starting",
            "login_data": None,
            "captcha_b64": None,
            "message": "Đang khởi động Playwright...",
        }
    )
    os.makedirs(config.DOWNLOAD_DIR, exist_ok=True)
    os.makedirs(config.STORE_DIR, exist_ok=True)

    qlvb_url = os.getenv("QLVB_URL", "https://egov1.laocai.gov.vn").rstrip("/")
    url_di = f"{qlvb_url}/document/xem-di-index?statustype=published&type=vanbandi"
    url_den = f"{qlvb_url}/document/xem-den-index?type=all"

    try:
        with CrawlHistory(config.CRAWLER_STATE_DB) as history:
            for direction in ("di", "den"):
                imported = history.import_legacy_json(get_history_file(direction), direction)
                if imported:
                    print(f"[crawler] Đã nhập {imported} lịch sử {direction.upper()} từ JSON.")

            await _ingest_leftovers(history)
            async with async_playwright() as playwright:
                browser = await playwright.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-setuid-sandbox"],
                )
                context = await browser.new_context(
                    viewport={"width": 1280, "height": 720},
                    ignore_https_errors=True,
                    accept_downloads=True,
                )
                context.set_default_timeout(config.CRAWLER_ACTION_TIMEOUT_SECONDS * 1000)
                context.set_default_navigation_timeout(config.CRAWLER_PAGE_TIMEOUT_SECONDS * 1000)
                page = await context.new_page()
                try:
                    await login(page, qlvb_url)
                    crawler_state["status"] = "crawling"
                    total_downloaded = 0
                    if mode in {"all", "di"}:
                        total_downloaded = await crawl_table(
                            page, qlvb_url, url_di, "di", limit, history, total_downloaded
                        )
                    if mode in {"all", "den"} and (limit == 0 or total_downloaded < limit):
                        total_downloaded = await crawl_table(
                            page, qlvb_url, url_den, "den", limit, history, total_downloaded
                        )
                    crawler_state.update(
                        {
                            "status": "done",
                            "captcha_b64": None,
                            "login_data": None,
                            "message": (
                                f"Hoàn tất: đã tải và nạp {total_downloaded} văn bản mới. "
                                "Checkpoint đã được lưu."
                            ),
                        }
                    )
                finally:
                    await context.close()
                    await browser.close()
    except Exception as exc:
        crawler_state.update(
            {
                "status": "error",
                "captcha_b64": None,
                "login_data": None,
                "message": f"Lỗi crawler: {type(exc).__name__}: {exc}",
            }
        )
