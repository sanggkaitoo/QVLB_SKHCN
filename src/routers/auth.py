"""Đăng nhập, đăng xuất, thông tin tài khoản hiện tại và đổi mật khẩu."""
import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field

from src.core import app_settings, config, security
from src.core.security import User

router = APIRouter()
_DUMMY_HASH = security.hash_password("placeholder-password")


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=100)
    password: str = Field(..., min_length=1, max_length=500)
    remember: bool = False


class PasswordRequest(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=500)
    new_password: str = Field(..., min_length=1, max_length=500)


class ProfileRequest(BaseModel):
    full_name: str | None = Field(None, max_length=200)
    email: str | None = Field(None, max_length=200)


def _set_cookie(response: Response, request: Request, token: str, lifetime: int, remember: bool) -> None:
    response.set_cookie(security.SESSION_COOKIE, token, max_age=lifetime if remember else None, httponly=True,
                        samesite="lax", secure=security.is_https(request), path="/")


@router.post("/login")
async def api_login(req: LoginRequest, request: Request, response: Response):
    ip = security.client_ip(request)
    key = f"{ip}|{req.username.strip().lower()}"
    if security.login_blocked(key) or security.login_blocked(ip):
        raise HTTPException(429, "Đăng nhập sai quá nhiều lần. Vui lòng thử lại sau.",
                            headers={"Retry-After": str(config.ADMIN_LOCKOUT_SECONDS)})
    user, password_hash = await asyncio.to_thread(security.find_user_for_login, req.username)
    # Luôn băm để thời gian phản hồi không lộ tài khoản có tồn tại hay không.
    valid = await asyncio.to_thread(security.verify_password, req.password,
                                    password_hash or _DUMMY_HASH)
    if not user or not password_hash or not valid:
        security.record_login_failure(key)
        security.record_login_failure(ip)
        security.audit(None, "login_failed", req.username[:100], ip=ip)
        raise HTTPException(401, "Tên đăng nhập hoặc mật khẩu không đúng.")
    if not user.is_active:
        raise HTTPException(403, "Tài khoản đã bị khoá. Vui lòng liên hệ quản trị viên.")
    security.clear_login_failures(key)
    token, lifetime = await asyncio.to_thread(security.create_session, user, request, req.remember)
    _set_cookie(response, request, token, lifetime, req.remember)
    security.audit(user, "login", ip=ip)
    return {"user": user.public()}


@router.post("/logout")
async def api_logout(request: Request, response: Response):
    user = security.current_user(request)
    await asyncio.to_thread(security.delete_session, request.cookies.get(security.SESSION_COOKIE))
    security.forget_session_cache()
    response.delete_cookie(security.SESSION_COOKIE, path="/")
    if user:
        security.audit(user, "logout", ip=security.client_ip(request))
    return {"ok": True}


@router.get("/me")
async def api_me(request: Request):
    user = await asyncio.to_thread(security.current_user, request)
    payload = {"user": user.public() if user else None,
               "anonymous_search": bool(app_settings.get("access.anonymous_search", False))}
    if user:
        payload["used_today"] = await asyncio.to_thread(security.used_today, user.id)
    return payload


@router.post("/password")
async def api_change_password(req: PasswordRequest, request: Request, user: User = Depends(security.require_login)):
    _, password_hash = await asyncio.to_thread(security.find_user_for_login, user.username)
    if not password_hash or not security.verify_password(req.current_password, password_hash):
        raise HTTPException(400, "Mật khẩu hiện tại không đúng.")
    problem = security.password_problem(req.new_password, user.username)
    if problem:
        raise HTTPException(400, problem)

    def update():
        from src.core import store
        with store.pg() as connection, connection.cursor() as cursor:
            cursor.execute("UPDATE users SET password_hash = %s, must_change_password = FALSE, updated_at = now() "
                           "WHERE id = %s", (security.hash_password(req.new_password), user.id))
        security.delete_user_sessions(user.id, keep_token=request.cookies.get(security.SESSION_COOKIE))

    await asyncio.to_thread(update)
    security.forget_session_cache()
    security.audit(user, "password_changed", ip=security.client_ip(request))
    return {"ok": True}


@router.patch("/profile")
async def api_profile(req: ProfileRequest, user: User = Depends(security.require_login)):
    def update():
        from src.core import store
        with store.pg() as connection, connection.cursor() as cursor:
            cursor.execute("UPDATE users SET full_name = %s, email = %s, updated_at = now() WHERE id = %s",
                           ((req.full_name or "").strip() or None, (req.email or "").strip() or None, user.id))
    await asyncio.to_thread(update)
    return {"ok": True}
