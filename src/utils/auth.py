"""HTTP Basic cho trang quản trị: từ chối mật khẩu yếu/mặc định và khóa tạm IP đăng nhập sai nhiều lần."""
import logging
import secrets
import threading
import time

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from src.core import config

logger = logging.getLogger(__name__)

security = HTTPBasic()
_failures: dict[str, list[float]] = {}
_failures_lock = threading.Lock()


def client_ip(request: Request) -> str:
    if config.TRUST_PROXY_HEADERS:
        forwarded = request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for", "").split(",")[0]
        if forwarded.strip():
            return forwarded.strip()
    return request.client.host if request.client else "unknown"


def _recent_failures(ip: str, now: float) -> list[float]:
    window = [moment for moment in _failures.get(ip, []) if now - moment < config.ADMIN_LOCKOUT_SECONDS]
    if window:
        _failures[ip] = window
    else:
        _failures.pop(ip, None)
    return window


def verify_admin(request: Request, credentials: HTTPBasicCredentials = Depends(security)):
    """Kiểm tra tài khoản/mật khẩu Admin."""
    if config.admin_password_is_weak():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Trang quản trị bị khóa: ADMIN_PASS chưa đặt hoặc quá yếu (tối thiểu 10 ký tự, không dùng mật khẩu mặc định).",
        )
    ip, now = client_ip(request), time.monotonic()
    with _failures_lock:
        if len(_recent_failures(ip, now)) >= config.ADMIN_MAX_FAILED_LOGINS:
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                                detail="Đăng nhập sai quá nhiều lần. Vui lòng thử lại sau.",
                                headers={"Retry-After": str(config.ADMIN_LOCKOUT_SECONDS)})
    is_user_ok = secrets.compare_digest(credentials.username.encode("utf-8"), config.ADMIN_USER.encode("utf-8"))
    is_pass_ok = secrets.compare_digest(credentials.password.encode("utf-8"), config.ADMIN_PASS.encode("utf-8"))
    if not (is_user_ok and is_pass_ok):
        with _failures_lock:
            _failures.setdefault(ip, []).append(now)
        logger.warning("Đăng nhập admin thất bại từ %s", ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Tài khoản hoặc mật khẩu không chính xác",
            headers={"WWW-Authenticate": "Basic"},
        )
    with _failures_lock:
        _failures.pop(ip, None)
    return credentials.username
