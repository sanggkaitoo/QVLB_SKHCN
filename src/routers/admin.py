import asyncio
import logging
from typing import Literal

import psycopg2.extras
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from src.core import config, runtime, store
from src.crawler.spider import crawler_state, run_spider
from src.utils.auth import verify_admin

logger = logging.getLogger(__name__)
router = APIRouter(dependencies=[Depends(verify_admin)])

_ACTIVE_STATES = {"starting", "waiting_login", "logging_in", "crawling"}
_crawl_task: asyncio.Task | None = None
_crawl_lock = asyncio.Lock()


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
    return {
        "status": crawler_state["status"],
        "message": crawler_state["message"],
        "captcha_b64": crawler_state["captcha_b64"],
    }


@router.post("/crawl/start")
async def api_crawl_start(req: CrawlRequest):
    global _crawl_task
    async with _crawl_lock:
        if (_crawl_task and not _crawl_task.done()) or crawler_state["status"] in _ACTIVE_STATES:
            return {"status": "error", "message": "Một tiến trình Crawler đang chạy!"}
        crawler_state.update(status="starting", message="Đang khởi động Crawler...")
        # Giữ tham chiếu để task không bị thu hồi giữa chừng và ghi nhận lỗi không mong đợi.
        _crawl_task = asyncio.create_task(run_spider(req.limit, req.mode), name="crawler")
        _crawl_task.add_done_callback(_log_crawl_result)
    return {"status": "success", "message": "Đã khởi động Crawler."}


def _log_crawl_result(task: asyncio.Task) -> None:
    if not task.cancelled() and task.exception():
        logger.error("Crawler dừng do lỗi", exc_info=task.exception())
        crawler_state.update(status="error", message="Crawler dừng do lỗi không mong đợi.", login_data=None, captcha_b64=None)


@router.post("/crawl/submit_login")
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
