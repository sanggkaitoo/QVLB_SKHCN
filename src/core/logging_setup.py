"""Ghi log ra tệp, không in ra console.

Tệp trong LOG_DIR (mặc định data/logs):
  app.log     mọi log của ứng dụng từ LOG_LEVEL trở lên (không gồm access log)
  error.log   chỉ WARNING trở lên — xem nhanh khi có sự cố
  access.log  mỗi request HTTP một dòng (uvicorn.access); tắt bằng LOG_ACCESS=false
Mỗi tệp tự xoay vòng khi vượt LOG_MAX_MB, giữ LOG_BACKUPS bản cũ đã nén gzip (app.log.1.gz, …).
print(), cảnh báo Python (warnings) và chữ ghi thẳng ra stdout/stderr của thư viện cũng được đưa vào log.
"""
from __future__ import annotations

import gzip
import logging
import logging.handlers
import os
import shutil
import sys
import warnings
from pathlib import Path

from src.core import config

FORMAT = "%(asctime)s %(levelname)-7s %(name)s [%(threadName)s]: %(message)s"
_ROOT = Path(__file__).resolve().parents[2]
_configured = False


def log_dir() -> Path:
    path = Path(config.LOG_DIR)
    return path if path.is_absolute() else _ROOT / path


def _gzip_rotator(source: str, dest: str) -> None:
    with open(source, "rb") as reader, gzip.open(dest, "wb") as writer:
        shutil.copyfileobj(reader, writer)
    os.remove(source)


def _file_handler(name: str, level: int) -> logging.Handler:
    handler = logging.handlers.RotatingFileHandler(
        log_dir() / name, maxBytes=config.LOG_MAX_MB * 1024 * 1024, backupCount=config.LOG_BACKUPS,
        encoding="utf-8", delay=True)
    handler.namer = lambda default: default + ".gz"
    handler.rotator = _gzip_rotator
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(FORMAT))
    return handler


class _NotAccess(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return record.name != "uvicorn.access"


class _StreamToLogger:
    """Thay sys.stdout/sys.stderr: mỗi dòng được ghi thành một bản ghi log."""

    def __init__(self, logger: logging.Logger, level: int, original):
        self.logger, self.level, self.original, self._buffer, self._busy = logger, level, original, "", False

    def write(self, text: str) -> int:
        if self._busy:  # lỗi phát sinh ngay trong lúc ghi log: ghi thẳng ra luồng gốc, tránh đệ quy
            return self.original.write(text)
        self._busy = True
        try:
            return self._write(text)
        finally:
            self._busy = False

    def _write(self, text: str) -> int:
        self._buffer += str(text)
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            line = line.rstrip("\r")
            if line.strip():
                self.logger.log(self.level, line.split("\r")[-1])  # thanh tiến trình: chỉ giữ trạng thái cuối
        return len(text)

    def flush(self) -> None:
        if self._buffer.strip():
            self.logger.log(self.level, self._buffer.strip())
        self._buffer = ""

    def isatty(self) -> bool:
        return False

    def fileno(self) -> int:
        return self.original.fileno()  # thư viện ghi thẳng vào file descriptor (C, subprocess)


def configure() -> Path:
    """Gọi một lần khi khởi động (src.main). Trả về thư mục log."""
    global _configured
    directory = log_dir()
    if _configured:
        return directory
    directory.mkdir(parents=True, exist_ok=True)
    level = getattr(logging, config.LOG_LEVEL, logging.INFO)

    app = _file_handler("app.log", level)
    app.addFilter(_NotAccess())
    error = _file_handler("error.log", logging.WARNING)
    error.addFilter(_NotAccess())
    handlers = [app, error]
    if config.LOG_CONSOLE:
        console = logging.StreamHandler(sys.__stderr__)
        console.setFormatter(logging.Formatter(FORMAT))
        console.setLevel(level)
        handlers.append(console)

    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    for handler in handlers:
        root.addHandler(handler)
    root.setLevel(level)

    # uvicorn tự gắn handler in ra console: gỡ đi, để log đi theo root (app.log/error.log).
    for name in ("uvicorn", "uvicorn.error"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
    access = logging.getLogger("uvicorn.access")
    access.handlers.clear()
    access.propagate = False
    if config.LOG_ACCESS:
        access.addHandler(_file_handler("access.log", logging.INFO))
        if config.LOG_CONSOLE:
            access.addHandler(handlers[-1])
    else:
        access.disabled = True

    # Thư viện ồn ào: chỉ giữ cảnh báo/lỗi. httpx còn ghi cả địa chỉ tải tệp QLVB có kèm token.
    for name in ("httpx", "httpcore", "urllib3", "openai", "anthropic", "sentence_transformers", "transformers",
                 "huggingface_hub", "filelock", "multipart", "watchfiles", "asyncio", "PIL"):
        logging.getLogger(name).setLevel(logging.WARNING)

    logging.captureWarnings(True)
    # Qdrant chạy cục bộ (127.0.0.1) qua http có khoá API: cảnh báo này lặp lại mỗi lần khởi động, không có ích.
    warnings.filterwarnings("ignore", message="Api key is used with an insecure connection")
    if not config.LOG_CONSOLE:
        sys.stdout = _StreamToLogger(logging.getLogger("stdout"), logging.INFO, sys.__stdout__)
        sys.stderr = _StreamToLogger(logging.getLogger("stderr"), logging.WARNING, sys.__stderr__)
    _configured = True
    logging.getLogger("docnexus").info("Ghi log vào %s (xoay vòng %s MB × %s bản)", directory,
                                       config.LOG_MAX_MB, config.LOG_BACKUPS)
    return directory
