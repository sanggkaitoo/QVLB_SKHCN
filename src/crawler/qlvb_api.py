"""Crawler qua API nội bộ của QLVB (thay cho thao tác giao diện bằng Playwright).

Hai pha, ghi tiến độ vào PostgreSQL (bảng crawl_items) sau từng văn bản:
  1. Kiểm kê: đọc danh sách Văn bản đi (xemdi) và Văn bản đến (văn thư, đã xử lý) -> biết tổng số,
     văn bản nào đã nạp/chưa nạp/lỗi. Chế độ nhanh chỉ đọc trang đầu và so sánh tổng số.
  2. Tải phần còn thiếu: đọc chi tiết từng văn bản, tải tệp đính kèm, nạp vào kho.

Chỉ gọi API đọc (danh sách, chi tiết, tải tệp). Không gọi các API đánh dấu "đã xem"
(click_xemdi, click_xemden, clickxem_vanban, notify/readed).
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
import shutil
import ssl
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
import psycopg2.extras

from src.core import config, store
from src.crawler import sso
from src.crawler.sso import CrawlStopped, LoginCancelled, check_stop, crawler_state
from src.services.ingest import SourceFile, ingest_document
from src.utils.extract import SUPPORTED_EXTENSIONS

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DirectionSpec:
    label: str
    list_path: str
    list_params: dict
    detail_type: str
    web_detail: str        # trang xem trên QLVB (chỉ để tham chiếu nguồn)
    date_field: str


DIRECTIONS = {
    "di": DirectionSpec("Văn bản đi", "/ioffice/Ajax/IworkHandler.ashx", {"type": "xemdi"}, "xemdi_chitiet",
                        "/document/xem-di-xem?documentId={id}", "ngaytao"),
    "den": DirectionSpec("Văn bản đến", "/ioffice/Ajax/IworkVanThuHandler.ashx", {"type": "xulyden", "trangthai": 2},
                         "xemden_chitiet", "/document/xem-den-xem?documentId={id}&type=done", "ngayden"),
}
_NORMAL_SECRECY = {"", "thường", "thuong", "binh thuong", "bình thường"}


class QlvbApiError(RuntimeError):
    pass


class SessionExpired(QlvbApiError):
    pass


def _ssl_context() -> ssl.SSLContext | bool:
    context = ssl.create_default_context()
    if os.path.exists(config.QLVB_CA_BUNDLE):
        context.load_verify_locations(cafile=config.QLVB_CA_BUNDLE)
    return context


def _redact(url: str) -> str:
    parts = urlsplit(url)
    query = urlencode([(k, "[REDACTED]" if "token" in k.lower() else v) for k, v in parse_qsl(parts.query)])
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))


def _date(value) -> str | None:
    match = re.match(r"\s*(\d{1,2})/(\d{1,2})/(\d{4})", str(value or ""))
    if not match:
        return None
    day, month, year = (int(part) for part in match.groups())
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def _clean(value) -> str:
    return " ".join(str(value or "").split())


def is_classified(do_mat) -> bool:
    return _clean(do_mat).lower() not in _NORMAL_SECRECY


class QlvbApiClient:
    def __init__(self, token: str):
        raw = token[7:].strip() if token.lower().startswith("bearer ") else token.strip()
        self._raw_token = raw
        self._client = httpx.AsyncClient(
            base_url=config.QLVB_API_BASE_URL,
            headers={"Authorization": f"Bearer {raw}", "Accept": "application/json, text/plain, */*"},
            timeout=config.CRAWLER_API_TIMEOUT_SECONDS,
            verify=_ssl_context(),
            follow_redirects=False,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def _get_value(self, path: str, params: dict):
        last_error = None
        for attempt in range(1, 4):
            try:
                response = await self._client.get(path, params=params)
            except httpx.HTTPError as exc:
                last_error = f"{type(exc).__name__}"
                await asyncio.sleep(min(2 ** attempt, 10))
                continue
            if response.status_code in {401, 403}:
                raise SessionExpired("Phiên QLVB đã hết hạn.")
            if response.status_code >= 500:
                last_error = f"HTTP {response.status_code}"
                await asyncio.sleep(min(2 ** attempt, 10))
                continue
            if response.status_code >= 400:
                raise QlvbApiError(f"QLVB từ chối yêu cầu (HTTP {response.status_code}).")
            try:
                payload = response.json()
            except ValueError as exc:
                # Without a valid token the gateway answers 200 "Not found application: ioffice".
                if "not found application" in response.text[:200].lower():
                    raise SessionExpired("Chưa đăng nhập hoặc phiên QLVB đã hết hạn.") from exc
                raise QlvbApiError("QLVB trả về dữ liệu không phải JSON.") from exc
            if isinstance(payload, dict) and payload.get("code") not in (None, "OK"):
                message = str(payload.get("message") or payload.get("code"))
                if re.search(r"(token|đăng nhập|unauthor|hết hạn)", message, re.IGNORECASE):
                    raise SessionExpired(message)
                raise QlvbApiError(f"QLVB báo lỗi: {message[:200]}")
            return payload.get("value") if isinstance(payload, dict) and "value" in payload else payload
        raise QlvbApiError(f"Không kết nối được API QLVB ({last_error}).")

    async def list_page(self, direction: str, page: int, length: int, skip: int,
                        newest_first: bool = False) -> tuple[int, list[dict]]:
        spec = DIRECTIONS[direction]
        params = {"page": page, "length": length, "term": "", "archiveSearch": "false", **spec.list_params,
                  "isLoading": "true", "defer": "true", "skip": skip}
        if newest_first:
            params.update(order_col=spec.date_field, order_type="desc")
        value = await self._get_value(spec.list_path, params) or {}
        rows = value.get("data") if isinstance(value, dict) else None
        if not isinstance(rows, list):
            raise QlvbApiError("QLVB không trả về danh sách văn bản.")
        return int(value.get("total") or 0), [row for row in rows if isinstance(row, dict) and row.get("macongvan")]

    async def detail(self, direction: str, source_id: str) -> dict:
        value = await self._get_value("/ioffice/Ajax/IworkHandler.ashx",
                                      {"type": DIRECTIONS[direction].detail_type, "documentId": source_id})
        if not isinstance(value, dict):
            raise QlvbApiError("QLVB không trả về chi tiết văn bản.")
        return value

    async def download(self, file_url: str, destination: str) -> int:
        parts = urlsplit(file_url)
        if parts.scheme != "https" or parts.hostname not in config.QLVB_STORAGE_HOSTS:
            raise QlvbApiError(f"Tệp không thuộc máy chủ lưu trữ QLVB: {parts.hostname}")
        params = dict(parse_qsl(parts.query))
        params["token"] = self._raw_token
        url = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
        limit = config.CRAWLER_MAX_FILE_MB * 1024 * 1024
        size = 0
        async with self._client.stream("GET", url, params=params, headers={"Accept": "*/*"}) as response:
            if response.status_code in {401, 403}:
                raise SessionExpired("Phiên QLVB đã hết hạn khi tải tệp.")
            if response.status_code >= 400:
                raise QlvbApiError(f"Không tải được tệp (HTTP {response.status_code}).")
            with open(destination, "wb") as stream:
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > limit:
                        raise QlvbApiError(f"Tệp vượt giới hạn {config.CRAWLER_MAX_FILE_MB} MB.")
                    stream.write(chunk)
        if size == 0:
            raise QlvbApiError("Tệp tải về rỗng.")
        return size


# --------------------------------------------------------------------------- database


def _fingerprint(row: dict) -> str:
    raw = "|".join([_clean(row.get("sohieu")), _clean(row.get("trichyeu")), ",".join(row.get("files") or [])])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _list_record(direction: str, row: dict) -> dict:
    spec = DIRECTIONS[direction]
    classified = is_classified(row.get("domat")) and config.CRAWLER_SKIP_CLASSIFIED
    return {
        "direction": direction, "source_id": str(row["macongvan"]).strip(),
        "so_ky_hieu": _clean(row.get("sohieu")) or None, "trich_yeu": _clean(row.get("trichyeu")) or None,
        "ngay": _date(row.get("ngaybh") or row.get(spec.date_field)),
        "co_quan": _clean(row.get("cqbh")) or None, "loai_vb": _clean(row.get("loaivanban")) or None,
        "do_mat": _clean(row.get("domat")) or None, "file_names": [str(f) for f in row.get("files") or []],
        "list_fingerprint": _fingerprint(row),
        "status": "skipped" if classified else "pending",
        "skip_reason": "Văn bản mật: không tải về và không gửi sang AI" if classified else None,
    }


def upsert_list_rows(direction: str, rows: list[dict]) -> tuple[int, int]:
    """Insert new items; re-queue done items whose list data (files, subject) changed. Returns (new, changed)."""
    if not rows:
        return 0, 0
    records = [_list_record(direction, row) for row in rows]
    with store.pg() as connection, connection.cursor() as cursor:
        new = changed = 0
        for record in records:
            cursor.execute(
                """INSERT INTO crawl_items (direction, source_id, so_ky_hieu, trich_yeu, ngay, co_quan, loai_vb,
                                            do_mat, file_names, list_fingerprint, status, skip_reason)
                   VALUES (%(direction)s, %(source_id)s, %(so_ky_hieu)s, %(trich_yeu)s, %(ngay)s, %(co_quan)s,
                           %(loai_vb)s, %(do_mat)s, %(file_names)s, %(list_fingerprint)s, %(status)s, %(skip_reason)s)
                   ON CONFLICT (direction, source_id) DO UPDATE SET
                       so_ky_hieu = EXCLUDED.so_ky_hieu, trich_yeu = EXCLUDED.trich_yeu, ngay = EXCLUDED.ngay,
                       co_quan = EXCLUDED.co_quan, loai_vb = EXCLUDED.loai_vb, do_mat = EXCLUDED.do_mat,
                       file_names = EXCLUDED.file_names, in_source = TRUE, last_seen_at = now(),
                       status = CASE
                           WHEN crawl_items.list_fingerprint IS DISTINCT FROM EXCLUDED.list_fingerprint
                                AND crawl_items.status IN ('done', 'failed') THEN 'pending'
                           WHEN EXCLUDED.status = 'skipped' AND crawl_items.status <> 'done' THEN 'skipped'
                           ELSE crawl_items.status END,
                       attempts = CASE WHEN crawl_items.list_fingerprint IS DISTINCT FROM EXCLUDED.list_fingerprint
                                       THEN 0 ELSE crawl_items.attempts END,
                       skip_reason = COALESCE(EXCLUDED.skip_reason, crawl_items.skip_reason),
                       list_fingerprint = EXCLUDED.list_fingerprint
                   RETURNING (xmax = 0) AS inserted""",
                record,
            )
            inserted = cursor.fetchone()[0]
            new += int(bool(inserted))
        # "changed" = rows touched this batch that went back to pending from done/failed
        cursor.execute("SELECT count(*) FROM crawl_items WHERE direction = %s AND source_id = ANY(%s) "
                       "AND status = 'pending' AND processed_at IS NOT NULL",
                       (direction, [record["source_id"] for record in records]))
        changed = int(cursor.fetchone()[0])
    return new, changed


def known_ids(direction: str, ids: list[str]) -> set[str]:
    with store.pg() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT source_id FROM crawl_items WHERE direction = %s AND source_id = ANY(%s)", (direction, ids))
        return {row[0] for row in cursor.fetchall()}


def _sweep(direction: str, mode: str) -> int:
    with store.pg() as connection, connection.cursor() as cursor:
        cursor.execute("INSERT INTO crawl_sweeps (direction, mode) VALUES (%s, %s) RETURNING id", (direction, mode))
        return int(cursor.fetchone()[0])


def _finish_sweep(sweep_id: int, **fields) -> None:
    columns = ", ".join(f"{key} = %s" for key in fields)
    with store.pg() as connection, connection.cursor() as cursor:
        cursor.execute(f"UPDATE crawl_sweeps SET {columns}, finished_at = now() WHERE id = %s", [*fields.values(), sweep_id])


def last_complete_total(direction: str) -> int | None:
    """Row total reported by QLVB in the latest complete sweep (the list may repeat a document)."""
    with store.pg() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT source_total FROM crawl_sweeps WHERE direction = %s AND completed "
                       "ORDER BY started_at DESC LIMIT 1", (direction,))
        row = cursor.fetchone()
        return int(row[0]) if row and row[0] is not None else None


def _mark_not_in_source(direction: str, seen: set[str]) -> None:
    with store.pg() as connection, connection.cursor() as cursor:
        cursor.execute("UPDATE crawl_items SET in_source = (source_id = ANY(%s)) WHERE direction = %s",
                       (list(seen), direction))


def recover_interrupted() -> int:
    """Crash recovery: items left 'processing' go back to pending; done items whose văn bản was deleted too."""
    with store.pg() as connection, connection.cursor() as cursor:
        cursor.execute("UPDATE crawl_items SET status = 'pending' WHERE status = 'processing'")
        recovered = cursor.rowcount
        cursor.execute("UPDATE crawl_items SET status = 'pending', processed_at = NULL "
                       "WHERE status = 'done' AND document_id IS NULL")
        return recovered + cursor.rowcount


def summary() -> dict:
    with store.pg() as connection, connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
        cursor.execute("""SELECT direction, status, count(*) AS n FROM crawl_items WHERE in_source
                          GROUP BY direction, status""")
        counts = {"di": {}, "den": {}}
        for row in cursor.fetchall():
            counts[row["direction"]][row["status"]] = row["n"]
        cursor.execute("""SELECT DISTINCT ON (direction) direction, mode, started_at, finished_at, source_total,
                                 seen, new_items, completed, error
                          FROM crawl_sweeps WHERE finished_at IS NOT NULL ORDER BY direction, started_at DESC""")
        sweeps = {row["direction"]: row for row in cursor.fetchall()}
        cursor.execute("""SELECT DISTINCT ON (direction) direction, source_total, seen FROM crawl_sweeps
                          WHERE completed AND mode = 'full' ORDER BY direction, started_at DESC""")
        full = {row["direction"]: row for row in cursor.fetchall()}
    output = {}
    for direction, spec in DIRECTIONS.items():
        by_status = counts.get(direction, {})
        sweep = sweeps.get(direction) or {}
        output[direction] = {
            "label": spec.label,
            "source_total": sweep.get("source_total"),
            # Distinct documents in the last complete full sweep (the list may repeat a document).
            "source_documents": (full.get(direction) or {}).get("seen"),
            "source_rows": (full.get(direction) or {}).get("source_total"),
            "inventoried": sum(by_status.values()),
            "done": by_status.get("done", 0),
            "pending": by_status.get("pending", 0) + by_status.get("processing", 0),
            "failed": by_status.get("failed", 0),
            "skipped": by_status.get("skipped", 0),
            "last_sweep_at": sweep.get("finished_at"),
            "last_sweep_mode": sweep.get("mode"),
            "last_sweep_completed": sweep.get("completed"),
        }
    return output


def list_items(status: str | None = None, direction: str | None = None, q: str = "", limit: int = 100,
               offset: int = 0) -> list[dict]:
    conditions, params = ["TRUE"], []
    if status:
        conditions.append("status = %s")
        params.append(status)
    if direction in DIRECTIONS:
        conditions.append("direction = %s")
        params.append(direction)
    if q:
        pattern = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        conditions.append("(so_ky_hieu ILIKE %s OR trich_yeu ILIKE %s)")
        params += [pattern, pattern]
    with store.pg() as connection, connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
        cursor.execute(
            f"""SELECT id, direction, source_id, so_ky_hieu, trich_yeu, ngay, co_quan, loai_vb, do_mat, status,
                       skip_reason, attempts, last_error, document_id, n_files, n_files_ingested, in_source,
                       processed_at
                FROM crawl_items WHERE {' AND '.join(conditions)}
                ORDER BY ngay DESC NULLS LAST, id DESC LIMIT %s OFFSET %s""",
            [*params, limit, offset],
        )
        return [dict(row) for row in cursor.fetchall()]


def requeue(item_id: int | None = None) -> int:
    """Đưa một văn bản (bất kỳ trạng thái kết thúc) hoặc mọi văn bản lỗi về hàng chờ."""
    with store.pg() as connection, connection.cursor() as cursor:
        if item_id is not None:
            cursor.execute("UPDATE crawl_items SET status = 'pending', attempts = 0, last_error = NULL "
                           "WHERE id = %s AND status IN ('failed', 'done', 'skipped')", (item_id,))
        else:
            cursor.execute("UPDATE crawl_items SET status = 'pending', attempts = 0 WHERE status = 'failed'")
        return cursor.rowcount


def _next_items(directions: list[str], limit: int, item_ids: list[int] | None = None) -> list[dict]:
    with store.pg() as connection, connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
        cursor.execute(
            """SELECT * FROM crawl_items WHERE status = 'pending' AND in_source AND direction = ANY(%s)
                   AND attempts < %s AND (%s::bigint[] IS NULL OR id = ANY(%s::bigint[]))
               ORDER BY ngay DESC NULLS LAST, id DESC LIMIT %s""",
            (directions, config.CRAWLER_API_MAX_ATTEMPTS, item_ids, item_ids, limit),
        )
        return [dict(row) for row in cursor.fetchall()]


def item_label(item_id: int) -> str | None:
    with store.pg() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT COALESCE(so_ky_hieu, source_id) FROM crawl_items WHERE id = %s", (item_id,))
        row = cursor.fetchone()
        return row[0] if row else None


def _update_item(item_id: int, **fields) -> None:
    if fields.get("processed_at") is True:
        fields["processed_at"] = datetime.now(timezone.utc)
    columns = ", ".join(f"{key} = %s" for key in fields)
    with store.pg() as connection, connection.cursor() as cursor:
        cursor.execute(f"UPDATE crawl_items SET {columns} WHERE id = %s", [*fields.values(), item_id])


# --------------------------------------------------------------------------- jobs


class _Session:
    """Lazily logs in (captcha via admin) and re-logs in when the token expires."""

    MAX_RENEWALS = 3

    def __init__(self):
        self.client: QlvbApiClient | None = None
        self.renewals = 0

    async def get(self) -> QlvbApiClient:
        if self.client is None:
            token = sso.access_token() or await sso.obtain_token()
            self.client = QlvbApiClient(token)
            crawler_state["status"] = "crawling"
        return self.client

    async def renew(self) -> QlvbApiClient:
        self.renewals += 1
        if self.renewals > self.MAX_RENEWALS:
            raise QlvbApiError("QLVB liên tục từ chối phiên đăng nhập; dừng để tránh hỏi captcha lặp lại.")
        if self.client:
            await self.client.close()
        self.client = None
        sso.forget_token()
        crawler_state.update(status="starting", message="Phiên QLVB đã hết hạn; cần đăng nhập lại để tiếp tục.")
        return await self.get()

    async def close(self) -> None:
        if self.client:
            await self.client.close()


async def _call(session: _Session, function, *args):
    """Run an API call; on expired session log in again once and retry."""
    try:
        return await function(await session.get(), *args)
    except SessionExpired:
        return await function(await session.renew(), *args)


async def _pause() -> None:
    if config.CRAWLER_API_DELAY_MS:
        await asyncio.sleep(config.CRAWLER_API_DELAY_MS / 1000)


def _page_size(value: int | None) -> int:
    return max(10, min(int(value or config.CRAWLER_API_PAGE_SIZE), config.CRAWLER_API_MAX_PAGE_SIZE))


async def _full_sweep(session: _Session, direction: str, mode: str = "full", page_size: int | None = None) -> dict:
    """Read the whole list in the server's default (stable) order.

    The server pages by page number and may return one row more than requested (the list of văn bản
    đến repeats the last row of each page as the first row of the next), so progress is counted in pages,
    never in rows returned, and rows are de-duplicated by macongvan.
    """
    spec = DIRECTIONS[direction]
    sweep_id = await asyncio.to_thread(_sweep, direction, mode)
    length, page, total, rows_read = _page_size(page_size), 1, None, 0
    seen: set[str] = set()
    new = changed = 0
    try:
        while True:
            check_stop()
            total, rows = await _call(session, lambda c, p=page, n=length: c.list_page(direction, p, n, (p - 1) * n))
            if page == 1 and rows and len(rows) < min(length, total):
                length = len(rows)  # the server caps the page size; page 1 is the same with the smaller size
            if not rows:
                break
            rows_read += len(rows)
            fresh = [row for row in rows if str(row["macongvan"]) not in seen]
            added, updated = await asyncio.to_thread(upsert_list_rows, direction, fresh)
            new, changed = new + added, changed + updated
            seen.update(str(row["macongvan"]) for row in rows)
            covered = min(page * length, total)
            crawler_state["message"] = (f"Kiểm kê {spec.label}: {covered:,}/{total:,} dòng, "
                                        f"{len(seen):,} văn bản").replace(",", ".")
            crawler_state["progress"] = {"phase": "inventory", "direction": direction, "done": covered, "total": total}
            if page * length >= total:
                break
            page += 1
            await _pause()
        completed = total is not None and (total == 0 or page * length >= total)
        if completed:
            await asyncio.to_thread(_mark_not_in_source, direction, seen)
        await asyncio.to_thread(_finish_sweep, sweep_id, source_total=total, seen=len(seen), new_items=new,
                                changed_items=changed, completed=completed)
        return {"direction": direction, "total": total, "seen": len(seen), "new": new, "changed": changed,
                "completed": completed}
    except Exception as exc:
        await asyncio.to_thread(_finish_sweep, sweep_id, source_total=total, seen=len(seen), new_items=new,
                                changed_items=changed, completed=False, error=f"{type(exc).__name__}: {exc}"[:500])
        raise


async def _quick_check(session: _Session, direction: str, page_size: int | None = None) -> dict:
    """Read the newest pages (sorted by date desc) until a page has no unknown document.

    New documents are added directly. If the row total then matches the last complete sweep plus the new
    rows, the inventory is up to date without a full sweep; otherwise fall back to a full sweep.
    """
    previous_total = await asyncio.to_thread(last_complete_total, direction)
    if previous_total is None:
        return await _full_sweep(session, direction, "full", page_size)
    length, page, new_rows, new_ids, total = _page_size(page_size), 1, 0, set(), None
    while page <= 20:
        check_stop()
        total, rows = await _call(session, lambda c, p=page, n=length: c.list_page(direction, p, n, (p - 1) * n,
                                                                              newest_first=True))
        ids = [str(row["macongvan"]) for row in rows]
        known = await asyncio.to_thread(known_ids, direction, ids)
        unknown = [row for row in rows if str(row["macongvan"]) not in known and str(row["macongvan"]) not in new_ids]
        if unknown:
            await asyncio.to_thread(upsert_list_rows, direction, unknown)
            new_ids.update(str(row["macongvan"]) for row in unknown)
            new_rows += len(unknown)
        crawler_state["message"] = f"Kiểm tra {DIRECTIONS[direction].label}: trang {page}, {len(new_ids)} văn bản mới"
        if not unknown or not rows or page * length >= total:
            break
        page += 1
        await _pause()
    if total == previous_total + new_rows:
        sweep_id = await asyncio.to_thread(_sweep, direction, "quick")
        await asyncio.to_thread(_finish_sweep, sweep_id, source_total=total, seen=len(new_ids), new_items=len(new_ids),
                                completed=True)
        return {"direction": direction, "total": total, "new": len(new_ids), "changed": 0, "completed": True,
                "quick": True}
    logger.info("Kiểm tra nhanh %s không khớp (tổng %s, trước %s, mới %s); quét toàn bộ",
                direction, total, previous_total, new_rows)
    result = await _full_sweep(session, direction, "full", page_size)
    result["new"] += len(new_ids)
    return result


def _begin(job: str, message: str) -> None:
    crawler_state.update({"status": "starting", "job": job, "stop_requested": False, "login_action": None,
                          "login_data": None, "captcha_b64": None, "progress": {}, "message": message})


def _end(status: str, message: str) -> None:
    crawler_state.update({"status": status, "captcha_b64": None, "login_data": None, "message": message})


async def run_sync(directions: list[str], mode: str = "quick", page_size: int | None = None) -> None:
    """Kiểm kê danh sách văn bản (quick: chỉ quét lại khi có thay đổi; full: luôn quét hết)."""
    _begin("api_sync", "Chuẩn bị kiểm kê danh sách văn bản...")
    session = _Session()
    results = []
    try:
        await asyncio.to_thread(recover_interrupted)
        for direction in directions:
            results.append(await (_quick_check(session, direction, page_size) if mode == "quick"
                                  else _full_sweep(session, direction, "full", page_size)))
        parts = []
        for r in results:
            label = DIRECTIONS[r["direction"]].label
            if r.get("quick"):
                parts.append(f"{label}: {r['new']:,} văn bản mới".replace(",", "."))
                continue
            duplicates = (r["total"] or 0) - r["seen"]
            parts.append((f"{label}: {r['seen']:,} văn bản trên QLVB"
                          + (f" ({r['total']:,} dòng, {duplicates:,} dòng lặp)" if duplicates > 0 else "")
                          + f", {r['new']:,} mới").replace(",", "."))
        _end("done", "Kiểm kê xong. " + "; ".join(parts) + ".")
    except (LoginCancelled, CrawlStopped) as exc:
        _end("cancelled", f"{exc} Phần đã kiểm kê được giữ nguyên.")
    except Exception as exc:
        logger.exception("API sync failed")
        _end("error", f"Kiểm kê dừng do lỗi: {type(exc).__name__}: {exc}"[:500])
    finally:
        await session.close()


def _raw_meta(direction: str, item: dict, detail: dict) -> dict:
    return {
        "so_ky_hieu": _clean(detail.get("SoHieu")) or item.get("so_ky_hieu") or "",
        "ngay_ban_hanh": _clean(detail.get("NgayBanHanh")) or (item["ngay"].strftime("%d/%m/%Y") if item.get("ngay") else ""),
        "trich_yeu": _clean(detail.get("TrichYeu")) or item.get("trich_yeu") or "",
        "co_quan_ban_hanh": _clean(detail.get("CoQuanBanHanh")) or item.get("co_quan") or "",
        "nguoi_ky": _clean(detail.get("NguoiKy")),
        "loai_van_ban": _clean(detail.get("LoaiVanBan")) or item.get("loai_vb") or "",
        "do_mat": _clean(detail.get("DoMat") or detail.get("TenCapDo")) or item.get("do_mat") or "",
        "ngay_den": _clean(detail.get("NgayDen")),
        "so_den": _clean(detail.get("SoDen")),
        "ma_dinh_danh": _clean(detail.get("MaDinhDanh")),
        "huong": direction,
        "qlvb_id": item["source_id"],
        "history_key": f"qlvb:{item['source_id']}",
        "source": "qlvb_api",
    }


def _safe_name(name: str, fallback: str) -> str:
    name = re.sub(r'[\\/*?:"<>|\x00-\x1f]', "_", os.path.basename(name or "")).strip().strip(".")
    return name[:150] or fallback


async def _process_item(session: _Session, item: dict) -> str:
    direction, source_id = item["direction"], item["source_id"]
    detail = await _call(session, lambda c: c.detail(direction, source_id))
    raw_meta = _raw_meta(direction, item, detail)
    if config.CRAWLER_SKIP_CLASSIFIED and is_classified(raw_meta["do_mat"]):
        await asyncio.to_thread(_update_item, item["id"], status="skipped", processed_at=True,
                                skip_reason="Văn bản mật: không tải về và không gửi sang AI")
        return "skipped"
    attachments = [a for a in detail.get("Attachments") or [] if isinstance(a, dict) and a.get("FilePath")]
    work_dir = os.path.join(config.DOWNLOAD_DIR, "api", f"{direction}_{source_id}")
    shutil.rmtree(work_dir, ignore_errors=True)
    os.makedirs(work_dir, exist_ok=True)
    try:
        sources, unsupported = [], []
        for index, attachment in enumerate(attachments, start=1):
            name = _safe_name(attachment.get("FileName"), f"tep_{index}")
            extension = name.rsplit(".", 1)[-1].lower() if "." in name else ""
            if extension not in SUPPORTED_EXTENSIONS:
                unsupported.append(name)
                continue
            check_stop()
            path = os.path.join(work_dir, f"{index:02d}_{name}")
            await _call(session, lambda c, url=attachment["FilePath"], p=path: c.download(url, p))
            sources.append(SourceFile(path, index))
            await _pause()
        if not sources:
            reason = "Không có tệp định dạng hỗ trợ" + (f" ({', '.join(unsupported[:5])})" if unsupported else "")
            await asyncio.to_thread(_update_item, item["id"], status="skipped", skip_reason=reason,
                                    n_files=len(attachments), processed_at=True)
            return "skipped"
        source_url = config.QLVB_URL + DIRECTIONS[direction].web_detail.format(id=source_id)
        outcome = await asyncio.to_thread(
            ingest_document, sources, huong=direction, raw_meta=raw_meta, source_url=source_url,
            force=False, source_key=f"{direction}|qlvb:{source_id}",
        )
        failed = outcome.get("failed_files") or []
        truncated = outcome.get("warnings") or []
        note = None
        if failed or unsupported or truncated:
            note = "; ".join(filter(None, [
                f"{len(failed)} tệp không trích xuất được" if failed else "",
                f"bỏ qua {len(unsupported)} tệp không hỗ trợ ({', '.join(unsupported[:3])})" if unsupported else "",
                *truncated[:2],
            ]))[:500]
        await asyncio.to_thread(_update_item, item["id"], status="done", document_id=outcome["document_id"],
                                n_files=len(attachments), n_files_ingested=len(sources) - len(failed),
                                last_error=note, processed_at=True)
        return "done"
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


async def run_download(directions: list[str], limit: int = 0, retry_failed: bool = False,
                       item_ids: list[int] | None = None) -> None:
    """Tải và nạp các văn bản chưa có trong kho (theo ngày mới nhất trước). limit=0: tất cả.

    item_ids: chỉ xử lý các văn bản này (nút "Tải lại" trên trang quản trị).
    """
    _begin("api_download", "Chuẩn bị tải văn bản còn thiếu...")
    session = _Session()
    counts = {"done": 0, "failed": 0, "skipped": 0}
    started = time.monotonic()
    try:
        await asyncio.to_thread(recover_interrupted)
        if retry_failed:
            await asyncio.to_thread(requeue)
        remaining_total = (len(item_ids) if item_ids else
                           sum(v["pending"] for k, v in (await asyncio.to_thread(summary)).items() if k in directions))
        target = min(limit, remaining_total) if limit else remaining_total
        processed = 0
        while not limit or processed < limit:
            check_stop()
            batch = await asyncio.to_thread(_next_items, directions, min(20, limit - processed) if limit else 20,
                                            item_ids)
            if not batch:
                break
            for item in batch:
                check_stop()
                if limit and processed >= limit:
                    break
                label = item.get("so_ky_hieu") or item["source_id"]
                crawler_state["message"] = f"Đang tải {processed + 1:,}/{target:,}: {label}".replace(",", ".")
                crawler_state["progress"] = {"phase": "download", "done": processed, "total": target,
                                             "ingested": counts["done"], "failed": counts["failed"],
                                             "skipped": counts["skipped"]}
                await asyncio.to_thread(_update_item, item["id"], status="processing", attempts=item["attempts"] + 1)
                try:
                    counts[await _process_item(session, item)] += 1
                except (LoginCancelled, CrawlStopped):
                    await asyncio.to_thread(_update_item, item["id"], status="pending", attempts=item["attempts"])
                    raise
                except Exception as exc:
                    logger.warning("Không xử lý được %s: %s", label, exc)
                    counts["failed"] += 1
                    await asyncio.to_thread(_update_item, item["id"], status="failed",
                                            last_error=f"{type(exc).__name__}: {exc}"[:500])
                processed += 1
        minutes = (time.monotonic() - started) / 60
        _end("done", f"Hoàn tất: {counts['done']} văn bản đã nạp, {counts['failed']} lỗi, "
                     f"{counts['skipped']} bỏ qua trong {minutes:.1f} phút.")
    except (LoginCancelled, CrawlStopped) as exc:
        _end("cancelled", f"{exc} Đã nạp {counts['done']} văn bản; phần còn lại vẫn ở trạng thái chờ.")
    except Exception as exc:
        logger.exception("API download failed")
        _end("error", f"Dừng do lỗi: {type(exc).__name__}: {exc}. Đã nạp {counts['done']} văn bản."[:500])
    finally:
        crawler_state["progress"] = {"phase": "download", "ingested": counts["done"], "failed": counts["failed"],
                                     "skipped": counts["skipped"]}
        await session.close()
