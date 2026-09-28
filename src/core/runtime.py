"""Bounded API work and cooperative deadlines shared by retrieval and LLM calls."""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import partial, wraps
import time

import anyio
from fastapi import HTTPException
from src.core import config

_deadline = ContextVar("rag_deadline", default=None)
_limiter = None


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


async def run_blocking(function, *args, **kwargs):
    global _limiter
    if _limiter is None:
        _limiter = anyio.CapacityLimiter(config.RAG_CONCURRENCY)
    try:
        _limiter.acquire_nowait()
    except anyio.WouldBlock:
        raise HTTPException(503, "Hệ thống đang xử lý yêu cầu khác. Vui lòng thử lại sau.", headers={"Retry-After": "3"})
    try:
        # Do not abandon the worker on disconnect: capacity stays reserved until it stops.
        return await anyio.to_thread.run_sync(partial(function, *args, **kwargs))
    finally:
        _limiter.release()
