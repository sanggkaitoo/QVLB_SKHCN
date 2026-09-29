"""Bounded API work and cooperative deadlines shared by retrieval and LLM calls."""
import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from functools import partial, wraps
import json
import logging
import threading
import time

import anyio
from fastapi import HTTPException
from src.core import config

logger = logging.getLogger(__name__)

_deadline = ContextVar("rag_deadline", default=None)

# Semaphores (not anyio limiters) so worker threads can release capacity themselves.
RAG_SLOTS = threading.BoundedSemaphore(config.RAG_CONCURRENCY)
UPLOAD_SLOTS = threading.BoundedSemaphore(config.UPLOAD_CONCURRENCY)

_BUSY_MESSAGE = "Hệ thống đang xử lý yêu cầu khác. Vui lòng thử lại sau."


def remaining(default=30.0):
    deadline = _deadline.get()
    if deadline is None:
        return float(default)
    seconds = deadline - time.monotonic()
    if seconds <= 0:
        raise TimeoutError("RAG processing deadline exceeded")
    return min(float(default), seconds)


@contextmanager
def budget(seconds):
    current = _deadline.get()
    token = _deadline.set(min(current, time.monotonic() + seconds) if current else time.monotonic() + seconds)
    try:
        yield
    finally:
        _deadline.reset(token)


def budgeted(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        with budget(config.AGENT_TIMEOUT_SECONDS):
            return function(*args, **kwargs)
    return wrapped


def _acquire(slots: threading.BoundedSemaphore) -> None:
    if not slots.acquire(blocking=False):
        raise HTTPException(503, _BUSY_MESSAGE, headers={"Retry-After": "3"})


async def run_blocking(function, *args, slots: threading.BoundedSemaphore | None = None, **kwargs):
    slots = slots or RAG_SLOTS
    _acquire(slots)
    try:
        # Do not abandon the worker on disconnect: capacity stays reserved until it stops.
        return await anyio.to_thread.run_sync(partial(function, *args, **kwargs))
    finally:
        slots.release()


_END = object()
KEEPALIVE = None


def open_event_stream(factory, *args, keepalive_seconds: float = 15.0,
                      slots: threading.BoundedSemaphore | None = None, **kwargs):
    """Run a blocking event generator in one worker thread and expose it as an async iterator.

    Capacity is reserved immediately (HTTP 503 before the response starts) and released by the
    worker when the generator finishes, even if the client never reads the stream. The whole
    generator runs in a single thread so context-variable deadlines stay valid across steps.
    Yields ``KEEPALIVE`` (None) when no event arrived for ``keepalive_seconds``.
    """
    slots = slots or RAG_SLOTS
    _acquire(slots)
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    client_gone = threading.Event()

    def emit(item):
        try:
            loop.call_soon_threadsafe(queue.put_nowait, item)
        except RuntimeError:  # event loop closed during shutdown
            client_gone.set()

    def worker():
        try:
            for event in factory(*args, **kwargs):
                if client_gone.is_set():
                    break
                emit(event)
        except Exception as exc:  # the generator is expected to report its own errors
            logger.exception("Event stream worker failed")
            emit({"type": "error", "message": "Hệ thống gặp lỗi khi xử lý yêu cầu.", "detail": type(exc).__name__})
        finally:
            slots.release()
            emit(_END)

    try:
        threading.Thread(target=worker, name="rag-stream", daemon=True).start()
    except Exception:
        slots.release()
        raise

    async def events():
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=keepalive_seconds)
                except asyncio.TimeoutError:
                    yield KEEPALIVE
                    continue
                if item is _END:
                    break
                yield item
        finally:
            client_gone.set()

    return events()


def sse(event) -> str:
    if event is KEEPALIVE:
        return ": keepalive\n\n"
    payload = json.dumps(event, ensure_ascii=False, default=str)
    return f"event: {event.get('type', 'message')}\ndata: {payload}\n\n"


async def sse_stream(events):
    async for event in events:
        yield sse(event)
