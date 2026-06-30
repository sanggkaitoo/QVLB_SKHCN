import asyncio # Thêm thư viện này
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from src.core import store
import psycopg2.extras
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

@router.get("/docs")
async def api_admin_docs(q: str = ""):
    try:
        with store.pg() as c, c.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            if q:
                # Tìm kiếm theo Số ký hiệu hoặc Trích yếu (có phân biệt từ khóa)
                cur.execute(
                    "SELECT so_ky_hieu, ngay_ban_hanh, loai_vb, trich_yeu "
                    "FROM documents "
                    "WHERE so_ky_hieu ILIKE %s OR trich_yeu ILIKE %s "
                    "ORDER BY ngay_ban_hanh DESC NULLS LAST LIMIT 100",
                    (f"%{q}%", f"%{q}%")
                )
            else:
                # Nếu không nhập từ khóa, lấy 100 văn bản mới nhất
                cur.execute(
                    "SELECT so_ky_hieu, ngay_ban_hanh, loai_vb, trich_yeu "
                    "FROM documents "
                    "ORDER BY ngay_ban_hanh DESC NULLS LAST LIMIT 100"
                )
            
            rows = cur.fetchall()
            # Xử lý format ngày tháng thành chuỗi để trả về JSON an toàn
            for r in rows:
                if r["ngay_ban_hanh"]:
                    r["ngay_ban_hanh"] = str(r["ngay_ban_hanh"])
            return JSONResponse(rows)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)