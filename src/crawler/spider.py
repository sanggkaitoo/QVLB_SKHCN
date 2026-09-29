from __future__ import annotations

import asyncio
import json
import os
import re
import unicodedata

from dotenv import load_dotenv
from playwright.async_api import async_playwright

from src.core import config
from src.crawler.history import CrawlHistory, source_key
from src.crawler.sso import (  # noqa: F401  (crawler_state re-exported for older imports)
    CrawlStopped, LoginCancelled, check_stop, crawler_state, find_login_form as _find_login_form,
    goto_with_retry as _goto_with_retry, login,
)
from src.services.document_fields import clean_placeholder
from src.services.ingest import ingest_download_dir

load_dotenv()

def get_history_file(direction: str) -> str:
    filename = "downloaded_records_di.json" if direction == "di" else "downloaded_records_den.json"
    return os.path.join(config.DOWNLOAD_DIR, filename)


def sanitize_filename(name: str) -> str:
    cleaned = re.sub(r'[\\/*?:"<>|]', "_", name or "").strip().strip(".")
    return cleaned[:180] or "khong_ten"


def _normalize_label(value: str) -> str:
    value = unicodedata.normalize("NFD", value or "")
    value = "".join(char for char in value if unicodedata.category(char) != "Mn")
    return re.sub(r"\s+", " ", value.lower()).strip()


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
                            # QLVB đôi khi hiển thị "undefined" trong ô trống; history_key giữ giá trị gốc.
                            "so_ky_hieu": clean_placeholder(document_ref),
                            "ngay_ban_hanh": clean_placeholder(issued_date),
                            "trich_yeu": clean_placeholder(subject),
                            "huong": direction,
                            "co_quan_ban_hanh": clean_placeholder(agency),
                            "file_goc": download.suggested_filename,
                            "source_url": page.url,
                            "history_key": record_key,
                            "file_index": index + 1,
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
            check_stop()
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
            "job": "spider",
            "login_data": None,
            "login_action": None,
            "stop_requested": False,
            "captcha_b64": None,
            "progress": {},
            "message": "Đang khởi động Playwright...",
        }
    )
    os.makedirs(config.DOWNLOAD_DIR, exist_ok=True)
    os.makedirs(config.STORE_DIR, exist_ok=True)

    qlvb_url = config.QLVB_URL.rstrip("/")
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
                    ignore_https_errors=config.CRAWLER_IGNORE_HTTPS_ERRORS,
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
    except (LoginCancelled, CrawlStopped) as exc:
        crawler_state.update({"status": "cancelled", "captcha_b64": None, "login_data": None,
                              "message": f"{exc} Tiến độ đã tải được giữ nguyên trong checkpoint."})
    except Exception as exc:
        crawler_state.update(
            {
                "status": "error",
                "captcha_b64": None,
                "login_data": None,
                "message": f"Lỗi crawler: {type(exc).__name__}: {exc}",
            }
        )
