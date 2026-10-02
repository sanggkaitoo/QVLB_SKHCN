import logging
import threading
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from src.core import config, logging_setup

# Log ghi ra tệp data/logs/*.log (xoay vòng, nén gzip), không in ra console. Gọi trước khi nạp các module khác.
logging_setup.configure()

from src.core import security  # noqa: E402
from src.routers import admin, aggregate, ai_settings, audio, auth, check, ocr, overview, search, users, web_routes  # noqa: E402

logger = logging.getLogger("docnexus")


def _warmup():
    try:
        from src.core import embedder
        embedder.warmup()
    except Exception:
        logger.exception("Không làm nóng được mô hình; mô hình sẽ nạp ở yêu cầu đầu tiên")


def _refresh_legal_index():
    try:
        from src.check import legal_registry
        if legal_registry.needs_refresh():
            legal_registry.build_index()
    except Exception:
        logger.exception("Không cập nhật được danh mục vbpl.vn")


@asynccontextmanager
async def lifespan(_: FastAPI):
    # Lần chạy đầu: tạo super admin từ ADMIN_USER/ADMIN_PASS (sau đó quản lý tài khoản trên trang quản trị).
    threading.Thread(target=security.ensure_super_admin, name="seed-super-admin", daemon=True).start()
    if config.LEGAL_REGISTRY_ENABLED:
        # Danh mục văn bản pháp luật (vbpl.vn) cho kiểm tra căn cứ: cập nhật nền khi quá hạn.
        threading.Thread(target=_refresh_legal_index, name="legal-index-check", daemon=True).start()
    if config.WARMUP_MODELS:
        # Nạp mô hình nền để yêu cầu đầu tiên không phải chờ khởi động nguội.
        threading.Thread(target=_warmup, name="model-warmup", daemon=True).start()
    yield


app = FastAPI(title="QLVB AI v4", lifespan=lifespan)
app.mount("/static", StaticFiles(directory="src/static"), name="static")

app.include_router(web_routes.router)
app.include_router(search.router, prefix="/api", tags=["Search"])
app.include_router(overview.router, prefix="/api", tags=["Overview"])
app.include_router(aggregate.router, prefix="/api", tags=["Aggregate"],
                   dependencies=[Depends(security.require("tools.aggregate"))])
app.include_router(check.router, prefix="/api/check", tags=["Check"],
                   dependencies=[Depends(security.require("tools.check"))])
app.include_router(audio.router, prefix="/api/audio", tags=["Audio"],
                   dependencies=[Depends(security.require("tools.audio"))])
app.include_router(ocr.router, prefix="/api/ocr", tags=["OCR"],
                   dependencies=[Depends(security.require("tools.ocr"))])
app.include_router(auth.router, prefix="/api/auth", tags=["Auth"])
app.include_router(ai_settings.public_router, prefix="/api/models", tags=["Models"])
app.include_router(users.router, prefix="/api/admin/users", tags=["Users"])
app.include_router(ai_settings.router, prefix="/api/admin/ai", tags=["AI settings"])
app.include_router(admin.router, prefix="/api/admin", tags=["Admin"])

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), geolocation=(), microphone=(self)",
}


@app.middleware("http")
async def security_headers(request: Request, call_next):
    if request.url.path.startswith("/api/") and not security.csrf_ok(request):
        return JSONResponse({"detail": "Thiếu header chống giả mạo yêu cầu (X-Requested-With)."}, status_code=403)
    response = await call_next(request)
    for header, value in _SECURITY_HEADERS.items():
        response.headers.setdefault(header, value)
    if request.url.path.startswith(("/api/admin", "/api/auth")) or request.url.path in {"/admin", "/login"}:
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
