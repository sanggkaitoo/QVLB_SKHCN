"""API hợp nhất Phase 1 (search + aggregate) và Phase 2 (check).
Chạy: uvicorn src.api:app --host 0.0.0.0 --port 8000
"""
import os
import tempfile
from fastapi import FastAPI, Query, UploadFile, File
from fastapi.responses import StreamingResponse, FileResponse, JSONResponse

from . import search, aggregate
from .check import format_check, content_check

app = FastAPI(title="QLVB AI v2")


@app.get("/")
async def index():
    p = os.path.join(os.path.dirname(__file__), "index.html")
    return FileResponse(p) if os.path.exists(p) else {"ok": True}


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
