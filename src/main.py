from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from src.routers import web_routes, search, admin, check, aggregate, audio, ocr
from src.core import store

app = FastAPI(title="QLVB AI v3")
app.mount("/static", StaticFiles(directory="src/static"), name="static")

# Gắn (Mount) các router vào app
app.include_router(web_routes.router)
app.include_router(search.router, prefix="/api", tags=["Search"])
app.include_router(aggregate.router, prefix="/api", tags=["Aggregate"])
app.include_router(check.router, prefix="/api/check", tags=["Check"])
app.include_router(admin.router, prefix="/api/admin", tags=["Admin"])
app.include_router(audio.router, prefix="/api/audio", tags=["Audio"])
app.include_router(ocr.router, prefix="/api/ocr", tags=["OCR"])


@app.get("/manifest.webmanifest", include_in_schema=False)
async def manifest():
    return FileResponse("src/static/pwa/manifest.webmanifest", media_type="application/manifest+json")

@app.get("/sw.js", include_in_schema=False)
async def service_worker():
    return FileResponse("src/static/pwa/sw.js", media_type="application/javascript")

@app.get("/offline.html", include_in_schema=False)
async def offline_page():
    return FileResponse("src/static/pwa/offline.html", media_type="text/html")
@app.get("/api/health")
async def health():
    return {"ok": True, "service": "qlvb_v3"}