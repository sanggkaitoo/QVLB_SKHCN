from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from src.core import store
from src.utils.auth import verify_admin

# Khóa TOÀN BỘ các API trong Router này bằng quyền Admin
router = APIRouter(dependencies=[Depends(verify_admin)])

class CrawlDemoRequest(BaseModel):
    limit: int

@router.get("/stats")
async def api_admin_stats():
    return JSONResponse(store.get_system_stats())

@router.post("/crawl/full")
async def api_crawl_full():
    # TODO: Gọi logic rpa/crawler.py
    return {"status": "success", "message": "Đã ra lệnh khởi chạy Crawler toàn bộ."}

@router.post("/crawl/demo")
async def api_crawl_demo(req: CrawlDemoRequest):
    # TODO: Gọi logic rpa/crawler.py với limit
    return {"status": "success", "message": f"Khởi chạy Demo {req.limit} văn bản."}