from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from src.core import store
try:
    from src.utils.auth import verify_admin
    router = APIRouter(dependencies=[Depends(verify_admin)])
except ImportError:
    router = APIRouter()

class CrawlDemoRequest(BaseModel):
    limit: int

@router.get("/stats")
async def api_admin_stats():
    return JSONResponse(store.get_system_stats())

@router.post("/crawl/full")
async def api_crawl_full():
    return {"status": "success", "message": "Đã ra lệnh khởi chạy Crawler toàn bộ."}

@router.post("/crawl/demo")
async def api_crawl_demo(req: CrawlDemoRequest):
    return {"status": "success", "message": f"Khởi chạy Demo {req.limit} văn bản."}