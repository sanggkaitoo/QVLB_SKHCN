import asyncio # Thêm thư viện này
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from src.core import store
# Import trực tiếp hàm async run_spider
from src.crawler.spider import run_spider, crawler_state 

try:
    from src.utils.auth import verify_admin
    router = APIRouter(dependencies=[Depends(verify_admin)])
except ImportError:
    router = APIRouter()

class CrawlRequest(BaseModel):
    limit: int
    mode: str = "all"

class LoginSubmitRequest(BaseModel):
    username: str
    password: str
    captcha: str

@router.get("/stats")
async def api_admin_stats():
    return JSONResponse(store.get_system_stats())

@router.get("/crawl/status")
async def api_crawl_status():
    return JSONResponse({
        "status": crawler_state["status"],
        "message": crawler_state["message"],
        "captcha_b64": crawler_state["captcha_b64"]
    })

@router.post("/crawl/start")
async def api_crawl_start(req: CrawlRequest):
    if crawler_state["status"] in ["starting", "waiting_login", "logging_in", "crawling"]:
        return {"status": "error", "message": "Một tiến trình Crawler đang chạy!"}
    
    # Truyền thêm req.mode vào run_spider
    asyncio.create_task(run_spider(req.limit, req.mode))
    return {"status": "success", "message": "Đã khởi động Crawler."}

@router.post("/crawl/submit_login")
async def api_crawl_submit_login(req: LoginSubmitRequest):
    if crawler_state["status"] != "waiting_login":
        return {"status": "error", "message": "Crawler không ở trạng thái chờ đăng nhập."}
    
    crawler_state["login_data"] = {
        "username": req.username,
        "password": req.password,
        "captcha": req.captcha
    }
    return {"status": "success", "message": "Đã gửi thông tin đăng nhập."}