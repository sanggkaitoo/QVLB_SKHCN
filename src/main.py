from fastapi import FastAPI
from src.routers import web_routes, search, admin, check, aggregate
from src.core import store

app = FastAPI(title="QLVB AI v3")

# Gắn (Mount) các router vào app
app.include_router(web_routes.router)
app.include_router(search.router, prefix="/api", tags=["Search"])
app.include_router(aggregate.router, prefix="/api", tags=["Aggregate"])
app.include_router(check.router, prefix="/api/check", tags=["Check"])
app.include_router(admin.router, prefix="/api/admin", tags=["Admin"])

@app.get("/api/health")
async def health():
    return {"ok": True, "service": "qlvb_v3"}