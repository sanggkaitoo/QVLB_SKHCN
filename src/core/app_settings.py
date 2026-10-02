"""Cấu hình ứng dụng sửa được trên trang quản trị (bảng app_settings), có cache ngắn.

Khoá API của nhà cung cấp AI được mã hoá bằng Fernet trước khi lưu. Khoá mã hoá lấy từ
APP_SECRET_KEY (.env) hoặc tự sinh một lần vào data/app_secret.key (quyền 600).
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
import threading
import time
from pathlib import Path

import psycopg2.extras
from cryptography.fernet import Fernet, InvalidToken

from src.core import store

logger = logging.getLogger(__name__)

_SECRET_FILE = Path("data/app_secret.key")
_CACHE_SECONDS = 10.0
_cache: dict[str, object] = {}
_cache_at = 0.0
_lock = threading.Lock()
_fernet: Fernet | None = None

DEFAULTS: dict[str, object] = {
    "access.anonymous_search": False,  # cho phép khách chưa đăng nhập dùng tra cứu
    "ai.roles": {},                   # {"answer": spec, "cheap": spec, "smart": spec, "fallback": spec}
    "ai.models": [],                  # mô hình admin đã bật: [{spec, name, featured, selectable, prices...}]
    "ai.default_model": None,         # mô hình mặc định cho người dùng khi tra cứu
    "ai.keys": {},                    # {"openrouter"|"openai"|"anthropic": token Fernet}
    "storage.keep_source_files": False,  # lưu bản gốc tệp khi crawl (STORE_DIR); mặc định không
}


def _load_all() -> dict[str, object]:
    with store.pg() as connection, connection.cursor() as cursor:
        cursor.execute("SELECT key, value FROM app_settings")
        return {key: value for key, value in cursor.fetchall()}


def all_settings() -> dict[str, object]:
    global _cache, _cache_at
    now = time.monotonic()
    with _lock:
        if now - _cache_at < _CACHE_SECONDS:
            return _cache
    try:
        loaded = _load_all()
    except Exception as exc:  # bảng chưa tạo hoặc database tạm lỗi: dùng giá trị mặc định/cũ
        logger.debug("Không đọc được app_settings: %s", exc)
        loaded = _cache or {}
    with _lock:
        _cache, _cache_at = loaded, now
    return loaded


def get(key: str, default=None):
    settings = all_settings()
    if key in settings:
        return settings[key]
    return DEFAULTS.get(key, default) if default is None else default


def put(key: str, value, user_id: int | None = None) -> None:
    global _cache_at
    with store.pg() as connection, connection.cursor() as cursor:
        cursor.execute(
            """INSERT INTO app_settings (key, value, updated_by, updated_at) VALUES (%s, %s, %s, now())
               ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_by = EXCLUDED.updated_by,
                                               updated_at = now()""",
            (key, psycopg2.extras.Json(value), user_id))
    with _lock:
        _cache_at = 0.0


def invalidate() -> None:
    global _cache_at
    with _lock:
        _cache_at = 0.0


# ------------------------------------------------------------------ khoá API mã hoá

def _cipher() -> Fernet:
    global _fernet
    if _fernet is not None:
        return _fernet
    secret = os.getenv("APP_SECRET_KEY", "").strip()
    if not secret:
        if _SECRET_FILE.exists():
            secret = _SECRET_FILE.read_text().strip()
        else:
            secret = Fernet.generate_key().decode()
            _SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
            _SECRET_FILE.write_text(secret)
            os.chmod(_SECRET_FILE, 0o600)
            logger.info("Đã tạo khoá mã hoá cấu hình tại %s", _SECRET_FILE)
    try:
        _fernet = Fernet(secret.encode())
    except ValueError:  # chuỗi tuỳ ý trong .env: suy ra khoá Fernet hợp lệ
        _fernet = Fernet(base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest()))
    return _fernet


def set_api_key(provider: str, api_key: str | None, user_id: int | None = None) -> None:
    keys = dict(get("ai.keys") or {})
    if api_key:
        keys[provider] = _cipher().encrypt(api_key.strip().encode()).decode()
    else:
        keys.pop(provider, None)
    put("ai.keys", keys, user_id)


def api_key(provider: str) -> str | None:
    token = (get("ai.keys") or {}).get(provider)
    if not token:
        return None
    try:
        return _cipher().decrypt(token.encode()).decode()
    except InvalidToken:
        logger.error("Không giải mã được khoá API %s (APP_SECRET_KEY đã đổi?)", provider)
        return None


def mask(secret: str | None) -> str | None:
    if not secret:
        return None
    return secret[:6] + "…" + secret[-4:] if len(secret) > 14 else "…" + secret[-2:]
