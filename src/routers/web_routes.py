from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from src.core import security

router = APIRouter()
templates = Jinja2Templates(directory="src/templates")


@router.get("/", response_class=HTMLResponse)
async def index_page(request: Request):
    return templates.TemplateResponse(request=request, name="index.html")


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse(request=request, name="login.html")


@router.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    """Trang quản trị: cần đăng nhập và có ít nhất một quyền quản trị (kho văn bản, tài khoản, cấu hình AI)."""
    user = security.current_user(request)
    if user is None:
        return RedirectResponse(f"/login?next={quote('/admin')}", status_code=303)
    if not user.permissions & security.ADMIN_AREA:
        return RedirectResponse("/?denied=admin", status_code=303)
    return templates.TemplateResponse(request=request, name="admin.html", context={"user": user.public()})
