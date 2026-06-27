"""API hợp nhất Phase 1 (search + aggregate) và Phase 2 (check).
Chạy: uvicorn src.api:app --host 0.0.0.0 --port 8000
"""
import os
import tempfile
from fastapi import FastAPI, Query, UploadFile, File
from fastapi.responses import StreamingResponse, FileResponse, JSONResponse
from pydantic import BaseModel

from . import search, aggregate
from .check import format_check, content_check

app = FastAPI(title="QLVB AI v2")

# User site
@app.get("/")
async def index():
    p = os.path.join(os.path.dirname(__file__), "index.html")
    return FileResponse(p) if os.path.exists(p) else {"ok": True}

# Admin site
@app.get("/admin")
async def admin_page():
    p = os.path.join(os.path.dirname(__file__), "admin.html")
    return FileResponse(p) if os.path.exists(p) else {"ok": False, "msg": "Thiếu file admin.html"}

@app.get("/api/admin/stats")
async def api_admin_stats():
    # Gọi hàm thống kê vừa tạo trong store.py
    return JSONResponse(store.get_system_stats())

# Models cho Crawler
class CrawlDemoRequest(BaseModel):
    limit: int

@app.post("/api/admin/crawl/full")
async def api_crawl_full():
    # TODO: Tích hợp logic gọi file rpa_logic.py (Playwright) của bạn ở đây
    # Ví dụ: background_tasks.add_task(run_full_crawl)
    return {"status": "success", "message": "Đã ra lệnh khởi chạy Crawler thu thập toàn bộ văn bản."}

@app.post("/api/admin/crawl/demo")
async def api_crawl_demo(req: CrawlDemoRequest):
    # TODO: Truyền biến req.limit vào hàm crawl để nó chỉ quét đúng số lượng trang/văn bản
    return {"status": "success", "message": f"Đã ra lệnh khởi chạy Crawler ở chế độ Demo với {req.limit} văn bản."}


@app.get("/api/health")
async def health():
    return {"ok": True, "service": "qlvb"}


# -------- Phase 1: tìm kiếm hybrid + trả lời grounded --------
@app.get("/api/search_stream")
async def api_search(q: str = Query(...), loai_vb: str | None = None,
                     huong: str | None = None):
    gen = search.answer_stream(q, loai_vb=loai_vb, huong=huong)
    return StreamingResponse(gen, media_type="text/event-stream")


# -------- Phase 1: tổng hợp đa văn bản (có minh chứng) --------
@app.get("/api/aggregate")
async def api_aggregate(q: str = Query(...)):
    return JSONResponse(aggregate.aggregate(q))


# -------- Phase 2: kiểm tra định dạng (.docx) --------
@app.post("/api/check/format")
async def api_check_format(file: UploadFile = File(...)):
    suffix = os.path.splitext(file.filename)[1] or ".docx"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await file.read())
        path = tmp.name
    try:
        return JSONResponse(format_check.check_format(path))
    finally:
        os.unlink(path)


# -------- Phase 2: kiểm tra nội dung / căn cứ --------
@app.post("/api/check/content")
async def api_check_content(file: UploadFile = File(...)):
    suffix = os.path.splitext(file.filename)[1] or ".docx"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await file.read())
        path = tmp.name
    try:
        return JSONResponse(content_check.check_content(path))
    finally:
        os.unlink(path)
