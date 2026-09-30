import asyncio
import csv
import io
import logging
import time
from typing import Literal

import psycopg2.extras
from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from src.core import config, runtime, security, store
from src.crawler import qlvb_api, sso
from src.crawler.spider import run_spider
from src.crawler.sso import ACTIVE_STATES as _ACTIVE_STATES, crawler_state

logger = logging.getLogger(__name__)
router = APIRouter(dependencies=[Depends(security.require("docs.view"))])
_CRAWL = [Depends(security.require("crawler.run"))]  # thao tác thay đổi: chạy crawler, đồng bộ, tải

_crawl_task: asyncio.Task | None = None
_crawl_lock = asyncio.Lock()
Direction = Literal["all", "di", "den"]


def _directions(value: str) -> list[str]:
    return ["di", "den"] if value == "all" else [value]


async def _launch(coroutine, name: str) -> dict:
    """Start one crawler job at a time (UI crawler or API crawler share the login/captcha state)."""
    global _crawl_task
    async with _crawl_lock:
        if (_crawl_task and not _crawl_task.done()) or crawler_state["status"] in _ACTIVE_STATES:
            coroutine.close()
            return {"status": "error", "message": "Một tác vụ crawler đang chạy!"}
        crawler_state.update(status="starting", message="Đang khởi động...", stop_requested=False)
        # Giữ tham chiếu để task không bị thu hồi giữa chừng và ghi nhận lỗi không mong đợi.
        _crawl_task = asyncio.create_task(coroutine, name=name)
        _crawl_task.add_done_callback(_log_crawl_result)
    return {"status": "success", "message": "Đã khởi động."}


class CrawlRequest(BaseModel):
    limit: int = Field(0, ge=0, le=100_000)
    mode: Literal["all", "di", "den"] = "all"


class LoginSubmitRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=200)
    password: str = Field(..., min_length=1, max_length=500)
    captcha: str = Field(..., min_length=1, max_length=50)


@router.get("/stats")
async def api_admin_stats():
    return await asyncio.to_thread(store.get_system_stats)


@router.get("/crawl/status")
async def api_crawl_status():
    expires_at = crawler_state.get("captcha_expires_at")
    return {
        "status": crawler_state["status"],
        "job": crawler_state.get("job"),
        "message": crawler_state["message"],
        "captcha_b64": crawler_state["captcha_b64"],
        "captcha_version": crawler_state.get("captcha_version", 0),
        "captcha_expires_in": max(0, int(expires_at - time.time())) if expires_at else None,
        "login_error": crawler_state.get("login_error"),
        "progress": crawler_state.get("progress") or {},
        "session": sso.session_info(),
    }


@router.post("/crawl/start", dependencies=_CRAWL)
async def api_crawl_start(req: CrawlRequest):
    """Crawler giao diện (Playwright) — phương án dự phòng."""
    return await _launch(run_spider(req.limit, req.mode), "crawler-ui")


@router.post("/crawl/captcha/refresh", dependencies=_CRAWL)
async def api_crawl_captcha_refresh():
    if crawler_state["status"] != "waiting_login":
        return {"status": "error", "message": "Không có phiên đăng nhập đang chờ."}
    crawler_state["login_action"] = "refresh"
    return {"status": "success", "message": "Đang tạo mã xác thực mới."}


@router.post("/crawl/cancel", dependencies=_CRAWL)
async def api_crawl_cancel():
    """Huỷ đăng nhập đang chờ hoặc dừng tác vụ đang chạy (dừng an toàn sau văn bản hiện tại)."""
    if crawler_state["status"] not in _ACTIVE_STATES:
        return {"status": "error", "message": "Không có tác vụ đang chạy."}
    if crawler_state["status"] == "waiting_login":
        crawler_state["login_action"] = "cancel"
    crawler_state["stop_requested"] = True
    crawler_state["message"] = "Đang dừng sau bước hiện tại..."
    return {"status": "success", "message": "Đã gửi yêu cầu dừng."}


class ApiSyncRequest(BaseModel):
    direction: Direction = "all"
    mode: Literal["quick", "full"] = "quick"
    page_size: int = Field(config.CRAWLER_API_PAGE_SIZE, ge=10, le=config.CRAWLER_API_MAX_PAGE_SIZE)


class ApiDownloadRequest(BaseModel):
    direction: Direction = "all"
    limit: int = Field(0, ge=0, le=100_000)
    retry_failed: bool = False


@router.get("/qlvb/summary")
async def api_qlvb_summary():
    return await asyncio.to_thread(qlvb_api.summary)


@router.post("/qlvb/sync", dependencies=_CRAWL)
async def api_qlvb_sync(req: ApiSyncRequest):
    return await _launch(qlvb_api.run_sync(_directions(req.direction), req.mode, req.page_size), "crawler-api-sync")


@router.post("/qlvb/download", dependencies=_CRAWL)
async def api_qlvb_download(req: ApiDownloadRequest):
    return await _launch(qlvb_api.run_download(_directions(req.direction), req.limit, req.retry_failed),
                         "crawler-api-download")


@router.get("/qlvb/items")
async def api_qlvb_items(status: Literal["pending", "processing", "done", "failed", "skipped"] | None = None,
                         direction: Literal["di", "den"] | None = None, q: str = Query("", max_length=200),
                         limit: int = Query(100, ge=1, le=500), offset: int = Query(0, ge=0)):
    rows = await asyncio.to_thread(qlvb_api.list_items, status, direction, q.strip(), limit, offset)
    for row in rows:
        for key in ("ngay", "processed_at"):
            row[key] = str(row[key]) if row.get(key) else None
    return rows


@router.post("/qlvb/items/{item_id}/retry", dependencies=_CRAWL)
async def api_qlvb_retry(item_id: int):
    """Đưa văn bản về hàng chờ và tải ngay nếu crawler đang rảnh.

    Crawler đang bận thì văn bản ở lại hàng chờ và được xử lý ở lượt "Tải phần còn thiếu" tiếp theo.
    """
    changed = await asyncio.to_thread(qlvb_api.requeue, item_id)
    if not changed:
        raise HTTPException(404, "Không tìm thấy văn bản ở trạng thái có thể tải lại.")
    label = await asyncio.to_thread(qlvb_api.item_label, item_id) or f"#{item_id}"
    started = await _launch(qlvb_api.run_download(["di", "den"], 0, False, [item_id]), "crawler-api-retry")
    if started["status"] == "success":
        return {"status": "success", "started": True, "message": f"Đang tải lại {label}."}
    return {"status": "success", "started": False,
            "message": f"Crawler đang chạy tác vụ khác; {label} đã vào hàng chờ, sẽ được tải ở lượt "
                       "“Tải phần còn thiếu” tiếp theo."}


@router.get("/qlvb/items.csv")
async def api_qlvb_items_csv(status: Literal["pending", "processing", "done", "failed", "skipped"] | None = None,
                             direction: Literal["di", "den"] | None = None):
    rows = await asyncio.to_thread(qlvb_api.list_items, status, direction, "", 100_000, 0)
    buffer = io.StringIO()
    buffer.write("\ufeff")  # Excel đọc đúng UTF-8
    writer = csv.writer(buffer)
    writer.writerow(["Hướng", "Số ký hiệu", "Ngày", "Trích yếu", "Cơ quan", "Trạng thái", "Lý do / lỗi", "Mã QLVB"])
    for row in rows:
        writer.writerow([row["direction"], row["so_ky_hieu"], row["ngay"], row["trich_yeu"], row["co_quan"],
                         row["status"], row["skip_reason"] or row["last_error"] or "", row["source_id"]])
    name = f"qlvb-{status or 'tat-ca'}-{direction or 'di-den'}.csv"
    return StreamingResponse(iter([buffer.getvalue()]), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": f'attachment; filename="{name}"'})


def _log_crawl_result(task: asyncio.Task) -> None:
    if not task.cancelled() and task.exception():
        logger.error("Crawler dừng do lỗi", exc_info=task.exception())
        crawler_state.update(status="error", message="Crawler dừng do lỗi không mong đợi.", login_data=None, captcha_b64=None)


@router.post("/crawl/submit_login", dependencies=_CRAWL)
async def api_crawl_submit_login(req: LoginSubmitRequest):
    if crawler_state["status"] != "waiting_login":
        return {"status": "error", "message": "Crawler không ở trạng thái chờ đăng nhập."}
    crawler_state["login_data"] = {"username": req.username, "password": req.password, "captcha": req.captcha}
    return {"status": "success", "message": "Đã gửi thông tin đăng nhập."}


def _list_documents(q: str) -> list[dict]:
    sql = ("SELECT d.id, d.so_ky_hieu, d.ngay_ban_hanh, d.loai_vb, d.huong, d.trich_yeu, d.ingest_status, "
           "d.index_version, d.n_files, d.n_chunks FROM documents d")
    params: tuple = ()
    if q:
        sql += " WHERE d.so_ky_hieu ILIKE %s OR d.trich_yeu ILIKE %s"
        pattern = "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        params = (pattern, pattern)
    sql += " ORDER BY d.ngay_ban_hanh DESC NULLS LAST, d.id DESC LIMIT 100"
    with store.pg() as connection, connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
        cursor.execute(sql, params)
        rows = [dict(row) for row in cursor.fetchall()]
    for row in rows:
        row["ngay_ban_hanh"] = str(row["ngay_ban_hanh"]) if row["ngay_ban_hanh"] else None
    return rows


@router.get("/docs")
async def api_admin_docs(q: str = Query("", max_length=200)):
    try:
        return await asyncio.to_thread(_list_documents, q.strip())
    except Exception:
        logger.exception("Không tải được danh sách văn bản")
        raise HTTPException(500, "Không tải được danh sách văn bản.")


@router.get("/docs/{document_id}/files")
async def api_admin_document_files(document_id: int):
    files = await asyncio.to_thread(store.document_files, document_id)
    if not files:
        raise HTTPException(404, "Không tìm thấy văn bản hoặc văn bản chưa có tệp.")
    return [{key: value for key, value in item.items() if key != "file_path"} for item in files]


@router.get("/health")
async def api_admin_health():
    return {"rag_slots_free": runtime.RAG_SLOTS._value, "upload_slots_free": runtime.UPLOAD_SLOTS._value,
            "index_version": config.INGEST_VERSION, "collection": config.RAG_COLLECTION}
