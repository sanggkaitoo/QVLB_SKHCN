"""Kho tệp gốc (STORE_DIR): có lưu bản gốc khi crawl hay không, dung lượng đang dùng, xoá bản gốc đã lưu.

Mặc định KHÔNG lưu: nội dung đã trích xuất nằm trong Postgres, vector trong Qdrant; tra cứu và nạp lại
(reindex) không cần tệp gốc. Cần xem bản gốc thì mở văn bản trên QLVB.

Xoá chỉ đụng tới đúng hai loại thư mục do pipeline nạp tạo ra trong STORE_DIR:
  <2 ký tự hex>/<sha256>/   bản gốc + metadata kèm (.meta.json)
  failed_ingest/            tệp nạp lỗi được cách ly
Mọi thứ khác (crawler_state.sqlite3, crawler_diagnostics, …) giữ nguyên; database không bị sửa.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from src.core import app_settings, config

logger = logging.getLogger(__name__)

KEEP_KEY = "storage.keep_source_files"
FAILED_DIR = "failed_ingest"
_BUCKET_RE = re.compile(r"[0-9a-f]{2}")
_DIGEST_RE = re.compile(r"[0-9a-f]{64}")
_USAGE_SECONDS = 60.0

_lock = threading.Lock()
_usage: dict | None = None
_usage_at = 0.0
_purge: dict = {"running": False}


def keep_enabled() -> bool:
    return bool(app_settings.get(KEEP_KEY, False))


def root() -> Path:
    return Path(config.STORE_DIR).resolve()


def is_stored(path: str) -> bool:
    """Tệp nằm trong kho bản gốc (đã lưu từ lần nạp trước)."""
    try:
        return bool(path) and Path(path).resolve().is_relative_to(root()) and os.path.isfile(path)
    except (OSError, ValueError):
        return False


def _content_dirs() -> list[Path]:
    """Các thư mục bản gốc do pipeline nạp tạo ra; tên phải đúng mẫu, không đi theo liên kết tượng trưng."""
    base = root()
    found = []
    try:
        buckets = [entry for entry in os.scandir(base) if entry.is_dir(follow_symlinks=False)]
    except FileNotFoundError:
        return []
    for bucket in buckets:
        if bucket.name == FAILED_DIR:
            found.append(Path(bucket.path))
            continue
        if not _BUCKET_RE.fullmatch(bucket.name):
            continue
        for entry in os.scandir(bucket.path):
            if (entry.is_dir(follow_symlinks=False) and _DIGEST_RE.fullmatch(entry.name)
                    and entry.name.startswith(bucket.name)):
                found.append(Path(entry.path))
    return found


def _measure(directory: Path) -> tuple[int, int]:
    files = size = 0
    for current, _, names in os.walk(directory):
        for name in names:
            try:
                stat = os.lstat(os.path.join(current, name))
            except OSError:
                continue
            files += 1
            size += stat.st_size
    return files, size


def usage(refresh: bool = False) -> dict:
    """Số tệp và dung lượng bản gốc đang lưu (không tính .meta.json vào số tệp)."""
    global _usage, _usage_at
    with _lock:
        if not refresh and _usage is not None and time.monotonic() - _usage_at < _USAGE_SECONDS:
            return _usage
    files = size = 0
    for directory in _content_dirs():
        for current, _, names in os.walk(directory):
            for name in names:
                try:
                    size += os.lstat(os.path.join(current, name)).st_size
                except OSError:
                    continue
                files += not name.endswith(".meta.json")
    result = {"files": files, "bytes": size, "path": str(root())}
    with _lock:
        _usage, _usage_at = result, time.monotonic()
    return result


def purge_status() -> dict:
    with _lock:
        return dict(_purge)


def _run_purge() -> None:
    deleted = freed = 0
    error = None
    try:
        for directory in _content_dirs():
            files, size = _measure(directory)
            shutil.rmtree(directory)  # rmtree không đi theo liên kết tượng trưng
            deleted, freed = deleted + files, freed + size
            with _lock:
                _purge.update(deleted_files=deleted, freed_bytes=freed)
        for bucket in os.scandir(root()):  # thư mục <hex> đã rỗng
            if bucket.is_dir(follow_symlinks=False) and _BUCKET_RE.fullmatch(bucket.name):
                try:
                    os.rmdir(bucket.path)
                except OSError:
                    pass
    except Exception as exc:
        logger.exception("Xoá tệp gốc dừng do lỗi")
        error = f"{type(exc).__name__}: {exc}"[:300]
    logger.info("Đã xoá %d tệp gốc, giải phóng %.1f MB", deleted, freed / 2**20)
    with _lock:
        _purge.update(running=False, finished_at=datetime.now(timezone.utc).isoformat(), error=error)
    usage(refresh=True)


def start_purge() -> str | None:
    """Xoá bản gốc đã lưu trong nền. Trả lý do từ chối, hoặc None khi đã bắt đầu."""
    if keep_enabled():
        return "Đang bật lưu tệp gốc: hãy tắt trước khi xoá."
    with _lock:
        if _purge.get("running"):
            return "Đang xoá tệp gốc."
        _purge.clear()
        _purge.update(running=True, started_at=datetime.now(timezone.utc).isoformat(), deleted_files=0,
                      freed_bytes=0, error=None)
    threading.Thread(target=_run_purge, name="purge-source-files", daemon=True).start()
    return None
