"""Tài khoản, phiên đăng nhập và phân quyền.

- Mật khẩu băm bằng scrypt (thư viện chuẩn), so sánh hằng thời gian.
- Phiên: token ngẫu nhiên trong cookie HttpOnly; database chỉ lưu SHA-256 của token.
- Quyền theo vai trò (ROLE_PERMISSIONS); kiểm tra bằng dependency `require(...)`.
- Chống CSRF: yêu cầu thay đổi dữ liệu phải gửi header X-Requested-With: DocNexus
  (trình duyệt không cho trang khác gửi header tuỳ ý mà không qua CORS, và app không bật CORS).
  Áp dụng cho mọi yêu cầu POST/PUT/PATCH/DELETE dưới /api, kể cả đăng nhập.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import psycopg2.extras
from fastapi import Depends, HTTPException, Request, status

from src.core import config, store

logger = logging.getLogger(__name__)

SESSION_COOKIE = "dn_session"
CSRF_HEADER = "x-requested-with"
CSRF_VALUE = "DocNexus"

ROLES = {
    "super_admin": "Super admin",
    "admin": "Quản trị viên",
    "operator": "Cán bộ vận hành",
    "staff": "Chuyên viên",
    "viewer": "Khách xem",
}
ROLE_RANK = {"super_admin": 4, "admin": 3, "operator": 2, "staff": 1, "viewer": 0}

TOOLS = {"tools.search", "tools.aggregate", "tools.check", "tools.audio", "tools.ocr"}
PERMISSION_LABELS = {
    "tools.search": "Tra cứu văn bản",
    "tools.aggregate": "Tổng hợp số liệu",
    "tools.check": "Kiểm tra dự thảo",
    "tools.audio": "Gỡ băng ghi âm",
    "tools.ocr": "Nhận dạng OCR",
    "models.choose": "Chọn mô hình AI khi tra cứu",
    "docs.view": "Xem kho văn bản và thống kê",
    "crawler.run": "Chạy crawler / đồng bộ QLVB",
    "users.manage": "Quản lý tài khoản",
    "settings.view": "Xem cấu hình AI",
    "settings.manage": "Sửa cấu hình AI, khoá API",
    "audit.view": "Xem nhật ký thao tác",
}
ROLE_PERMISSIONS = {
    "super_admin": set(PERMISSION_LABELS),
    "admin": TOOLS | {"models.choose", "docs.view", "crawler.run", "users.manage", "settings.view", "audit.view"},
    "operator": TOOLS | {"models.choose", "docs.view", "crawler.run"},
    "staff": TOOLS | {"models.choose"},
    "viewer": {"tools.search"},
}
ADMIN_AREA = {"docs.view", "crawler.run", "users.manage", "settings.view", "audit.view"}


@dataclass
class User:
    id: int
    username: str
    full_name: str | None
    email: str | None
    role: str
    is_active: bool
    daily_quota: int
    must_change_password: bool = False
    permissions: set[str] = field(default_factory=set)

    def can(self, permission: str) -> bool:
        return permission in self.permissions

    def public(self) -> dict:
        return {"id": self.id, "username": self.username, "full_name": self.full_name, "email": self.email,
                "role": self.role, "role_label": ROLES.get(self.role, self.role), "daily_quota": self.daily_quota,
                "must_change_password": self.must_change_password, "permissions": sorted(self.permissions),
                "admin_area": bool(self.permissions & ADMIN_AREA)}


# ------------------------------------------------------------------ mật khẩu

_SCRYPT = {"n": 2 ** 14, "r": 8, "p": 1}


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, dklen=32, **_SCRYPT)
    return "scrypt${n}${r}${p}${salt}${hash}".format(
        **_SCRYPT, salt=base64.b64encode(salt).decode(), hash=base64.b64encode(digest).decode())


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, n, r, p, salt, expected = encoded.split("$")
        if scheme != "scrypt":
            return False
        digest = hashlib.scrypt(password.encode("utf-8"), salt=base64.b64decode(salt), dklen=32,
                                n=int(n), r=int(r), p=int(p))
        return hmac.compare_digest(digest, base64.b64decode(expected))
    except (ValueError, TypeError):
        return False


def password_problem(password: str, username: str = "") -> str | None:
    if len(password) < 10:
        return "Mật khẩu cần tối thiểu 10 ký tự."
    if password.lower() in {"matkhau123", "password12", "1234567890", "admin12345", "changeme123"}:
        return "Mật khẩu quá phổ biến."
    if username and username.lower() in password.lower():
        return "Mật khẩu không được chứa tên đăng nhập."
    if password.isdigit() or password.isalpha():
        return "Mật khẩu cần có cả chữ và số (hoặc ký tự đặc biệt)."
    return None


# ------------------------------------------------------------------ người dùng

_USER_COLUMNS = "id, username, full_name, email, role, is_active, daily_quota, must_change_password"


def _user(row: dict | None) -> User | None:
    if not row:
        return None
    return User(id=int(row["id"]), username=row["username"], full_name=row["full_name"], email=row["email"],
                role=row["role"], is_active=bool(row["is_active"]), daily_quota=int(row["daily_quota"] or 0),
                must_change_password=bool(row["must_change_password"]),
                permissions=set(ROLE_PERMISSIONS.get(row["role"], set())))


def get_user(user_id: int) -> User | None:
    with store.pg() as connection, connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
        cursor.execute(f"SELECT {_USER_COLUMNS} FROM users WHERE id = %s", (user_id,))
        return _user(cursor.fetchone())


def find_user_for_login(username: str) -> tuple[User | None, str | None]:
    with store.pg() as connection, connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
        cursor.execute(f"SELECT {_USER_COLUMNS}, password_hash FROM users WHERE lower(username) = lower(%s)",
                       (username.strip(),))
        row = cursor.fetchone()
        return (_user(row), row["password_hash"]) if row else (None, None)


def count_users() -> int:
    with store.pg() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM users")
        return int(cursor.fetchone()[0])


def ensure_super_admin() -> str:
    """Tạo super admin từ ADMIN_USER/ADMIN_PASS khi chưa có tài khoản nào (lần chạy đầu)."""
    try:
        if count_users():
            return "exists"
    except Exception as exc:  # migration chưa chạy
        logger.warning("Chưa đọc được bảng users: %s", exc)
        return "unavailable"
    if config.admin_password_is_weak():
        logger.warning("Chưa có tài khoản nào và ADMIN_PASS yếu/chưa đặt: không tạo super admin. "
                       "Đặt ADMIN_PASS mạnh rồi khởi động lại, hoặc chạy scripts/create_user.py.")
        return "weak_password"
    with store.pg() as connection, connection.cursor() as cursor:
        cursor.execute("INSERT INTO users (username, full_name, password_hash, role) VALUES (%s, %s, %s, 'super_admin')",
                       (config.ADMIN_USER, "Super admin", hash_password(config.ADMIN_PASS)))
    logger.info("Đã tạo tài khoản super admin '%s' từ ADMIN_USER/ADMIN_PASS", config.ADMIN_USER)
    return "created"


def used_today(user_id: int) -> int:
    with store.pg() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT count(*) FROM rag_query_logs WHERE user_id = %s AND created_at >= date_trunc('day', now())",
                       (user_id,))
        return int(cursor.fetchone()[0])


def check_quota(user: User | None) -> None:
    if user and user.daily_quota and used_today(user.id) >= user.daily_quota:
        raise HTTPException(429, f"Bạn đã dùng hết hạn mức {user.daily_quota} câu hỏi AI hôm nay.")


def audit(user: User | None, action: str, target: str | None = None, detail: dict | None = None,
          ip: str | None = None) -> None:
    try:
        with store.pg() as connection, connection.cursor() as cursor:
            cursor.execute("INSERT INTO audit_log (user_id, username, action, target, detail, ip) VALUES (%s, %s, %s, %s, %s, %s)",
                           (user.id if user else None, user.username if user else None, action, target,
                            psycopg2.extras.Json(detail or {}), ip))
    except Exception as exc:
        logger.warning("Không ghi được nhật ký: %s", exc)


# ------------------------------------------------------------------ phiên

def client_ip(request: Request) -> str:
    if config.TRUST_PROXY_HEADERS:
        forwarded = request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for", "").split(",")[0]
        if forwarded.strip():
            return forwarded.strip()
    return request.client.host if request.client else "unknown"


def is_https(request: Request) -> bool:
    if request.url.scheme == "https":
        return True
    return config.TRUST_PROXY_HEADERS and request.headers.get("x-forwarded-proto", "").lower() == "https"


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def create_session(user: User, request: Request, remember: bool) -> tuple[str, int]:
    token = secrets.token_urlsafe(32)
    lifetime = config.SESSION_REMEMBER_DAYS * 86400 if remember else config.SESSION_HOURS * 3600
    with store.pg() as connection, connection.cursor() as cursor:
        cursor.execute("DELETE FROM user_sessions WHERE expires_at < now()")
        cursor.execute("INSERT INTO user_sessions (token_hash, user_id, expires_at, ip, user_agent) VALUES (%s, %s, %s, %s, %s)",
                       (_token_hash(token), user.id, datetime.now(timezone.utc) + timedelta(seconds=lifetime),
                        client_ip(request), (request.headers.get("user-agent") or "")[:300]))
        cursor.execute("UPDATE users SET last_login_at = now() WHERE id = %s", (user.id,))
    return token, lifetime


def delete_session(token: str | None) -> None:
    if token:
        with store.pg() as connection, connection.cursor() as cursor:
            cursor.execute("DELETE FROM user_sessions WHERE token_hash = %s", (_token_hash(token),))


def delete_user_sessions(user_id: int, keep_token: str | None = None) -> None:
    with store.pg() as connection, connection.cursor() as cursor:
        cursor.execute("DELETE FROM user_sessions WHERE user_id = %s AND token_hash <> %s",
                       (user_id, _token_hash(keep_token) if keep_token else ""))


_session_cache: dict[str, tuple[float, int | None]] = {}
_session_lock = threading.Lock()


def _session_user_id(token: str) -> int | None:
    key = _token_hash(token)
    now = time.monotonic()
    with _session_lock:
        cached = _session_cache.get(key)
        if cached and now - cached[0] < 15:
            return cached[1]
    with store.pg() as connection, connection.cursor() as cursor:
        cursor.execute("UPDATE user_sessions SET last_seen_at = now() WHERE token_hash = %s AND expires_at > now() "
                       "RETURNING user_id", (key,))
        row = cursor.fetchone()
    user_id = int(row[0]) if row else None
    with _session_lock:
        if len(_session_cache) > 5000:
            _session_cache.clear()
        _session_cache[key] = (now, user_id)
    return user_id


def forget_session_cache() -> None:
    with _session_lock:
        _session_cache.clear()


def current_user(request: Request) -> User | None:
    """Người dùng của phiên hiện tại (None nếu chưa đăng nhập / phiên hết hạn / tài khoản bị khoá)."""
    if hasattr(request.state, "user"):
        return request.state.user
    user = None
    token = request.cookies.get(SESSION_COOKIE)
    if token and len(token) < 200:
        try:
            user_id = _session_user_id(token)
            user = get_user(user_id) if user_id else None
            if user and not user.is_active:
                user = None
        except Exception as exc:
            logger.warning("Không kiểm tra được phiên: %s", exc)
    request.state.user = user
    return user


def require(*permissions: str, allow_anonymous_setting: str | None = None):
    """Dependency: đăng nhập và có ít nhất một trong các quyền."""
    def dependency(request: Request) -> User | None:
        user = current_user(request)
        if user is None:
            if allow_anonymous_setting:
                from src.core import app_settings
                if app_settings.get(allow_anonymous_setting, False):
                    return None
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Vui lòng đăng nhập để sử dụng chức năng này.")
        if permissions and not any(user.can(permission) for permission in permissions):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Tài khoản của bạn không có quyền dùng chức năng này.")
        return user
    return dependency


def require_login(request: Request) -> User:
    user = current_user(request)
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Vui lòng đăng nhập.")
    return user


def can_manage(actor: User, target_role: str, target_id: int | None = None) -> bool:
    """Super admin quản lý mọi tài khoản; quản trị viên chỉ quản lý vai trò thấp hơn mình."""
    if not actor.can("users.manage"):
        return False
    if actor.role == "super_admin":
        return True
    return ROLE_RANK.get(target_role, 99) < ROLE_RANK[actor.role] and target_id != actor.id


# ------------------------------------------------------------------ chống dò mật khẩu

_failures: dict[str, list[float]] = {}
_failures_lock = threading.Lock()


def login_blocked(key: str) -> bool:
    now = time.monotonic()
    with _failures_lock:
        window = [moment for moment in _failures.get(key, []) if now - moment < config.ADMIN_LOCKOUT_SECONDS]
        _failures[key] = window
        return len(window) >= config.ADMIN_MAX_FAILED_LOGINS


def record_login_failure(key: str) -> None:
    with _failures_lock:
        _failures.setdefault(key, []).append(time.monotonic())


def clear_login_failures(key: str) -> None:
    with _failures_lock:
        _failures.pop(key, None)


def csrf_ok(request: Request) -> bool:
    if request.method in {"GET", "HEAD", "OPTIONS"}:
        return True
    return request.headers.get(CSRF_HEADER, "") == CSRF_VALUE


def dependency_user(user: User = Depends(require_login)) -> User:
    return user
