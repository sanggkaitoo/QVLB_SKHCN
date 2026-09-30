"""Gọi Claude trực tiếp qua SDK chính thức của Anthropic (tách riêng khỏi client OpenAI-compatible).

- Luôn dùng stream (tránh timeout với max_tokens lớn); chat() gom lại bản cuối.
- Bật fallback phía máy chủ (`fallbacks="default"`) cho các model hỗ trợ: khi bộ lọc an toàn
  từ chối, Anthropic tự chạy lại yêu cầu trên model dự phòng trong cùng lời gọi.
- stop_reason "refusal" (cả chuỗi từ chối) và "max_tokens" được báo lỗi để llm.py thử model kế tiếp.
- Không gửi temperature: các model Claude mới từ chối tham số lấy mẫu khác mặc định.
"""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterator

import anthropic

logger = logging.getLogger(__name__)

FALLBACK_BETA = "server-side-fallback-2026-07-01"
_FALLBACK_MODELS = ("claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5")
_EFFORT_PREFIXES = ("claude-fable", "claude-mythos", "claude-opus-5", "claude-opus-4-6", "claude-opus-4-7",
                    "claude-opus-4-8", "claude-sonnet-5", "claude-sonnet-4-6")
# Model luôn/ mặc định suy nghĩ: phần suy nghĩ dùng chung max_tokens nên cần dư địa.
_THINKING_PREFIXES = ("claude-fable", "claude-mythos", "claude-opus-5", "claude-sonnet-5")

# Giá tham khảo (USD / 1 triệu token) khi chưa lấy được danh mục trực tiếp.
KNOWN_MODELS = [
    {"id": "claude-opus-5-5", "name": "Claude Opus 5.5", "prompt": 4.0, "completion": 20.0, "context": 1_000_000},
    {"id": "claude-sonnet-5-5", "name": "Claude Sonnet 5.5", "prompt": 2.0, "completion": 10.0, "context": 1_000_000},
    {"id": "claude-fable-5-1", "name": "Claude Fable 5.1", "prompt": 10.0, "completion": 50.0, "context": 1_000_000},
    {"id": "claude-opus-5", "name": "Claude Opus 5", "prompt": 5.0, "completion": 25.0, "context": 1_000_000},
    {"id": "claude-sonnet-5", "name": "Claude Sonnet 5", "prompt": 2.0, "completion": 10.0, "context": 1_000_000},
    {"id": "claude-haiku-4-5", "name": "Claude Haiku 4.5", "prompt": 1.0, "completion": 5.0, "context": 200_000},
]

_clients: dict[str, anthropic.Anthropic] = {}
_clients_lock = threading.Lock()


def _client(api_key: str) -> anthropic.Anthropic:
    with _clients_lock:
        client = _clients.get(api_key)
        if client is None:
            client = anthropic.Anthropic(api_key=api_key, max_retries=2)
            _clients.clear()  # khoá đổi thì bỏ client cũ
            _clients[api_key] = client
        return client


def _thinks(model: str) -> bool:
    return model.startswith(_THINKING_PREFIXES)


def _request(model: str, system: str, user: str, max_tokens: int, effort: str | None) -> dict:
    params: dict = {
        "model": model,
        # Model suy nghĩ cần dư địa cho phần suy nghĩ, không thì câu trả lời bị cắt.
        "max_tokens": max(max_tokens, 16000) if _thinks(model) else max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    if effort and model.startswith(_EFFORT_PREFIXES):
        params["output_config"] = {"effort": effort}
    if model.startswith(_FALLBACK_MODELS):
        params["betas"] = [FALLBACK_BETA]
        params["fallbacks"] = "default"
    return params


def _check_stop(message) -> None:
    if message.stop_reason == "refusal":
        raise RuntimeError(f"Claude ({message.model}) từ chối trả lời yêu cầu này")
    if message.stop_reason == "max_tokens":
        raise RuntimeError("Model output exceeded max_tokens")


def stream(api_key: str, model: str, system: str, user: str, *, max_tokens: int, timeout: float,
           effort: str | None = None, on_usage: Callable[[int, int], None] | None = None) -> Iterator[str]:
    """Sinh từng đoạn text. Báo lỗi nếu toàn bộ chuỗi model từ chối hoặc bị cắt vì max_tokens."""
    client = _client(api_key).with_options(timeout=timeout, max_retries=0)
    with client.beta.messages.stream(**_request(model, system, user, max_tokens, effort)) as response:
        yield from response.text_stream
        message = response.get_final_message()
    if on_usage and message.usage:
        on_usage(message.usage.input_tokens or 0, message.usage.output_tokens or 0)
    if message.model != model:
        logger.info("Claude %s đã chuyển sang model dự phòng %s", model, message.model)
    _check_stop(message)


def chat(api_key: str, model: str, system: str, user: str, *, max_tokens: int, timeout: float,
         effort: str | None = None, on_usage: Callable[[int, int], None] | None = None) -> str:
    return "".join(stream(api_key, model, system, user, max_tokens=max_tokens, timeout=timeout,
                          effort=effort, on_usage=on_usage))


def list_models(api_key: str) -> list[dict]:
    """Danh mục model Claude tài khoản dùng được (giá lấy từ bảng tham khảo nếu có)."""
    known = {item["id"]: item for item in KNOWN_MODELS}
    output = []
    for model in _client(api_key).with_options(timeout=15).models.list():
        base = known.get(model.id) or next((item for key, item in known.items() if model.id.startswith(key)), {})
        output.append({"id": model.id, "name": model.display_name, "prompt": base.get("prompt"),
                       "completion": base.get("completion"), "context": model.max_input_tokens})
    return output
