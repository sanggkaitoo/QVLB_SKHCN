import logging
import threading
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from src.core import config
from src.routers import admin, aggregate, audio, check, ocr, search, web_routes

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("docnexus")


def _warmup():
    try:
        from src.core import embedder
        embedder.warmup()
    except Exception:
        logger.exception("Không làm nóng được mô hình; mô hình sẽ nạp ở yêu cầu đầu tiên")


@asynccontextmanager
async def lifespan(_: FastAPI):
    if config.admin_password_is_weak():
        logger.warning("ADMIN_PASS chưa đặt hoặc quá yếu: trang quản trị sẽ bị khóa cho đến khi đổi mật khẩu.")
    if config.WARMUP_MODELS:
        # Nạp mô hình nền để yêu cầu đầu tiên không phải chờ khởi động nguội.
        threading.Thread(target=_warmup, name="model-warmup", daemon=True).start()
    yield


app = FastAPI(title="QLVB AI v4", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="src/static"), name="static")

app.include_router(web_routes.router)
app.include_router(search.router, prefix="/api", tags=["Search"])
app.include_router(aggregate.router, prefix="/api", tags=["Aggregate"])
app.include_router(check.router, prefix="/api/check", tags=["Check"])
app.include_router(admin.router, prefix="/api/admin", tags=["Admin"])
app.include_router(audio.router, prefix="/api/audio", tags=["Audio"])
app.include_router(ocr.router, prefix="/api/ocr", tags=["OCR"])

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), geolocation=(), microphone=(self)",
}


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    for header, value in _SECURITY_HEADERS.items():
        response.headers.setdefault(header, value)
    if request.url.path.startswith("/api/admin") or request.url.path == "/admin":
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/manifest.webmanifest", include_in_schema=False)
async def manifest():
    return FileResponse("src/static/pwa/manifest.webmanifest", media_type="application/manifest+json")


@app.get("/sw.js", include_in_schema=False)
async def service_worker():
    return FileResponse("src/static/pwa/sw.js", media_type="application/javascript",
                        headers={"Cache-Control": "no-cache"})


@app.get("/offline.html", include_in_schema=False)
async def offline_page():
    return FileResponse("src/static/pwa/offline.html", media_type="text/html")


@app.get("/api/health")
async def health():
    return {"ok": True, "service": "docnexus", "rag_engine": "agentic", "routing_mode": config.AGENT_ROUTING_MODE,
            "collection": config.RAG_COLLECTION, "index_version": config.INGEST_VERSION}
