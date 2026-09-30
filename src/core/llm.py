"""LLM client nhiều nhà cung cấp: OpenRouter (mặc định), OpenAI (ChatGPT) và Anthropic (Claude).

Chỉ định model bằng "nhà_cung_cấp:model", ví dụ "openai:gpt-5-mini", "anthropic:claude-sonnet-5-5";
không có tiền tố nghĩa là OpenRouter (ví dụ "google/gemini-2.5-flash"). Có thể dùng vai trò:
  @answer (trả lời tra cứu), @cheap (lập kế hoạch, kiểm chứng, trích xuất), @smart (thẩm định),
  @fallback (dự phòng) — admin gán trên trang quản trị, mặc định lấy từ .env.

Hàm chính:
  - chat(): trả text
  - chat_stream(): sinh từng đoạn text khi model trả về (stream thật)
  - extract_json(): ép model trả JSON, parse an toàn -> dict
"""
import json
import logging
import os
import re
import threading
from contextlib import contextmanager
from contextvars import ContextVar

from openai import OpenAI

from src.core import anthropic_provider, app_settings, config
from src.core.runtime import remaining

logger = logging.getLogger(__name__)

# Mỗi tính năng gọi model riêng; "inputs" là loại dữ liệu model bắt buộc phải nhận được.
TASKS = {
    "answer": {"label": "Tra cứu – soạn câu trả lời", "inputs": ["text"], "env": "LLM_MAIN"},
    "plan": {"label": "Tra cứu – phân tích câu hỏi, lập kế hoạch, chấm bằng chứng", "inputs": ["text"], "env": "LLM_CHEAP"},
    "verify": {"label": "Tra cứu – kiểm chứng câu trả lời với nguồn", "inputs": ["text"], "env": "LLM_CHEAP"},
    "aggregate": {"label": "Tổng hợp số liệu – lập kế hoạch, trích số liệu", "inputs": ["text"], "env": "LLM_CHEAP"},
    "check": {"label": "Kiểm tra dự thảo – thẩm định nội dung", "inputs": ["text"], "env": "LLM_SMART"},
    "transcribe": {"label": "Gỡ băng – chuyển giọng nói thành văn bản", "inputs": ["audio"], "env": "LLM_TRANSCRIBE"},
    "summary": {"label": "Gỡ băng – tóm tắt thành biên bản", "inputs": ["text"], "env": "LLM_SMART"},
    "ocr": {"label": "OCR – nhận dạng chữ từ ảnh/PDF scan", "inputs": ["image"], "env": "LLM_OCR"},
    "metadata": {"label": "Nhập liệu – trích metadata văn bản khi crawl", "inputs": ["text"], "env": "LLM_CHEAP"},
    "fallback": {"label": "Dự phòng khi model trả lời lỗi", "inputs": ["text"], "env": "LLM_FALLBACK"},
}
_ALIASES = {"cheap": "plan", "smart": "check", "main": "answer"}  # tên vai trò cũ
ANSWER, PLAN, VERIFY, AGGREGATE, CHECK = "@answer", "@plan", "@verify", "@aggregate", "@check"
TRANSCRIBE, SUMMARY, OCR, METADATA, FALLBACK = "@transcribe", "@summary", "@ocr", "@metadata", "@fallback"
CHEAP, SMART = PLAN, CHECK
PROVIDERS = {"openrouter": "OpenRouter", "openai": "OpenAI (ChatGPT)", "anthropic": "Anthropic (Claude)",
             "gemini": "Google Gemini (trực tiếp)", "local": "Máy chủ OCR nội bộ"}
_ENV_KEYS = {"openrouter": ("OPENROUTER_API_KEY", "LLM_API_KEY"), "openai": ("OPENAI_API_KEY",),
             "anthropic": ("ANTHROPIC_API_KEY",), "gemini": ("GEMINI_API_KEY",), "local": ()}

_usage_trace = ContextVar("llm_usage_trace", default=None)
_request_scope = ContextVar("llm_request_scope", default=None)


@contextmanager
def track_usage():
    usage = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "responses_with_usage": 0}
    token = _usage_trace.set(usage)
    try:
        yield usage
    finally:
        _usage_trace.reset(token)


@contextmanager
def request_scope(user_id: int | None = None, answer_model: str | None = None):
    """Gắn người dùng và model họ chọn cho các lời gọi trong một yêu cầu (log + định tuyến)."""
    scope = {"user_id": user_id, "answer_model": answer_model, "served_model": None}
    token = _request_scope.set(scope)
    try:
        yield scope
    finally:
        _request_scope.reset(token)


def current_scope() -> dict:
    return _request_scope.get() or {}


def _record_usage(prompt_tokens: int, completion_tokens: int) -> None:
    usage = _usage_trace.get()
    if usage is None:
        return
    usage["prompt_tokens"] += prompt_tokens or 0
    usage["completion_tokens"] += completion_tokens or 0
    usage["responses_with_usage"] += 1


def _record_openai_usage(raw_usage) -> None:
    if raw_usage is not None:
        _record_usage(getattr(raw_usage, "prompt_tokens", 0), getattr(raw_usage, "completion_tokens", 0))


def _count_call() -> None:
    usage = _usage_trace.get()
    if usage is not None:
        usage["calls"] += 1


# ------------------------------------------------------------------ định tuyến model

def split_spec(spec: str) -> tuple[str, str]:
    provider, sep, model = spec.partition(":")
    if sep and provider in PROVIDERS:
        return provider, model
    return "openrouter", spec


def task_name(spec: str) -> str | None:
    if not spec.startswith("@"):
        return None
    name = _ALIASES.get(spec[1:], spec[1:])
    if name not in TASKS:
        raise ValueError(f"Không có tính năng {spec}")
    return name


def resolve(spec: str | None) -> str:
    """Đổi "@answer", "@ocr"... thành model cụ thể theo cấu hình admin (mặc định lấy từ .env)."""
    spec = spec or ANSWER
    task = task_name(spec)
    if task:
        return (app_settings.get("ai.roles") or {}).get(task) or getattr(config, TASKS[task]["env"])
    return spec


def provider_key(provider: str) -> str | None:
    if provider == "local":
        return "local"
    stored = app_settings.api_key(provider)
    if stored:
        return stored
    for name in _ENV_KEYS[provider]:
        if os.getenv(name):
            return os.getenv(name)
    return None


def key_source(provider: str) -> str | None:
    if provider == "local":
        return "env" if config.OCR_SERVER_URL else None
    if app_settings.api_key(provider):
        return "admin"
    return "env" if any(os.getenv(name) for name in _ENV_KEYS[provider]) else None


_openai_clients: dict[tuple[str, str], OpenAI] = {}
_openai_lock = threading.Lock()


def _openai_client(provider: str) -> OpenAI:
    api_key = provider_key(provider)
    if not api_key:
        raise RuntimeError(f"Chưa cấu hình khoá API cho {PROVIDERS[provider]}")
    base_url = config.LLM_BASE_URL if provider == "openrouter" else "https://api.openai.com/v1"
    with _openai_lock:
        client = _openai_clients.get((provider, api_key))
        if client is None:
            for key in [key for key in _openai_clients if key[0] == provider]:
                _openai_clients.pop(key)
            client = OpenAI(base_url=base_url, api_key=api_key, timeout=120.0, max_retries=3)
            _openai_clients[(provider, api_key)] = client
        return client


def _openai_reasoning(model: str) -> bool:
    # Model suy luận của OpenAI không nhận temperature và tính cả token suy luận vào giới hạn đầu ra.
    return model.startswith(("o1", "o3", "o4", "gpt-5"))


def _openai_params(provider: str, model: str, temperature: float, max_tokens: int, effort: str | None) -> dict:
    if provider == "openrouter":
        return {"temperature": temperature, "max_tokens": max_tokens}
    if _openai_reasoning(model):
        params = {"max_completion_tokens": max(max_tokens, 16000)}
        if effort:
            params["reasoning_effort"] = effort
        return params
    return {"temperature": temperature, "max_completion_tokens": max_tokens}


def _messages(system: str, user: str) -> list[dict]:
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _mark_served(spec: str) -> None:
    scope = _request_scope.get()
    if scope is not None:
        scope["served_model"] = spec


# ------------------------------------------------------------------ API công khai

def chat(system: str, user: str, model: str | None = None,
         temperature: float = 0.1, timeout: float | None = None,
         max_tokens: int | None = None, effort: str | None = None) -> str:
    spec = resolve(model)
    provider, model_id = split_spec(spec)
    limit = min(max_tokens or config.LLM_MAX_OUTPUT_TOKENS, config.LLM_MAX_OUTPUT_TOKENS)
    seconds = remaining(timeout or 120)
    _count_call()
    if provider == "anthropic":
        api_key = provider_key("anthropic")
        if not api_key:
            raise RuntimeError("Chưa cấu hình khoá API cho Anthropic (Claude)")
        return anthropic_provider.chat(api_key, model_id, system, user, max_tokens=limit, timeout=seconds,
                                       effort=effort, on_usage=_record_usage)
    request_client = _openai_client(provider).with_options(timeout=seconds, max_retries=0)
    resp = request_client.chat.completions.create(
        model=model_id, messages=_messages(system, user), **_openai_params(provider, model_id, temperature, limit, effort))
    provider_error = getattr(resp, "error", None)
    if provider_error:
        raise RuntimeError(f"Provider returned an error payload: {str(provider_error)[:500]}")
    if not resp.choices:
        raise RuntimeError("Provider returned no completion choices")
    _record_openai_usage(resp.usage)
    if resp.choices[0].finish_reason == "length":
        raise RuntimeError("Model output exceeded LLM_MAX_OUTPUT_TOKENS")
    return resp.choices[0].message.content


def _openai_stream(provider: str, model_id: str, system: str, user: str, temperature: float, idle: float):
    request_client = _openai_client(provider).with_options(timeout=idle, max_retries=0)
    stream = request_client.chat.completions.create(
        model=model_id, messages=_messages(system, user), stream=True, stream_options={"include_usage": True},
        **_openai_params(provider, model_id, temperature, config.LLM_MAX_OUTPUT_TOKENS, None))
    finish_reason = None
    try:
        for chunk in stream:
            remaining(config.AGENT_TIMEOUT_SECONDS)
            provider_error = getattr(chunk, "error", None)
            if provider_error:
                raise RuntimeError(f"Provider returned an error payload: {str(provider_error)[:500]}")
            if getattr(chunk, "usage", None):
                _record_openai_usage(chunk.usage)
            if not chunk.choices:
                continue
            choice = chunk.choices[0]
            finish_reason = choice.finish_reason or finish_reason
            text = getattr(choice.delta, "content", None)
            if text:
                yield text
    finally:
        close = getattr(stream, "close", None)
        if close:
            close()
    if finish_reason == "length":
        raise RuntimeError("Model output exceeded LLM_MAX_OUTPUT_TOKENS")


def answer_candidates() -> list[str]:
    chosen = current_scope().get("answer_model")
    return [spec for spec in (chosen, ANSWER, FALLBACK) if spec]


def chat_stream(system: str, user: str, models: list[str] | None = None,
                temperature: float = 0.1, timeout: float | None = None):
    """Yield text deltas. Falls back to the next model only if nothing was emitted yet."""
    candidates = list(dict.fromkeys(resolve(m) for m in (models or answer_candidates()) if m))
    errors = []
    for spec in candidates:
        provider, model_id = split_spec(spec)
        emitted = False
        try:
            # Read timeout bounds silence between chunks, including the wait for the first token.
            idle = min(remaining(timeout or config.AGENT_TIMEOUT_SECONDS), config.LLM_STREAM_IDLE_TIMEOUT_SECONDS)
            _count_call()
            if provider == "anthropic":
                api_key = provider_key("anthropic")
                if not api_key:
                    raise RuntimeError("Chưa cấu hình khoá API cho Anthropic (Claude)")
                # Model Claude suy nghĩ trước khi viết: cho chờ token đầu lâu hơn.
                parts = anthropic_provider.stream(api_key, model_id, system, user,
                                                  max_tokens=config.LLM_MAX_OUTPUT_TOKENS,
                                                  timeout=max(idle, remaining(60)), on_usage=_record_usage)
            else:
                parts = _openai_stream(provider, model_id, system, user, temperature, idle)
            for text in parts:
                remaining(config.AGENT_TIMEOUT_SECONDS)
                emitted = True
                yield text
            if emitted:
                _mark_served(spec)
                return
            errors.append(f"{spec}: empty")
        except Exception as exc:
            if emitted:
                raise
            errors.append(f"{spec}: {type(exc).__name__}")
            logger.warning("Streaming model %s failed: %s", spec, exc)
    raise RuntimeError("Không model nào trả lời được: " + ", ".join(errors))


_JSON_RE = re.compile(r"\{.*\}|\[.*\]", re.DOTALL)


def parse_json(raw: str | None):
    if not raw:
        return None
    raw = re.sub(r"^```(json)?|```$", "", raw.strip(), flags=re.MULTILINE).strip()
    try:
        return json.loads(raw)
    except Exception:
        match = _JSON_RE.search(raw)
        if match:
            try:
                return json.loads(match.group(0))
            except Exception:
                return None
    return None


def extract_json(system: str, user: str, model: str | None = None,
                 timeout: float | None = None, max_tokens: int | None = None) -> dict | list | None:
    """Gọi model với yêu cầu CHỈ trả JSON; bóc tách an toàn."""
    raw = chat(
        system=system + "\n\nCHỈ trả về JSON hợp lệ, không kèm giải thích, không markdown.",
        user=user, model=model or CHEAP, temperature=0.0, timeout=timeout,
        max_tokens=max_tokens, effort="low",
    )
    return parse_json(raw)


def chat_with_fallback(system: str, user: str, models: list[str] | None = None,
                       temperature: float = 0.1) -> str:
    candidates = [resolve(m) for m in (models or answer_candidates())]
    errors = []
    for model in dict.fromkeys(candidate for candidate in candidates if candidate):
        try:
            answer = chat(system, user, model=model, temperature=temperature,
                          timeout=config.AGENT_TIMEOUT_SECONDS)
            if answer and answer.strip():
                _mark_served(model)
                return answer.strip()
        except Exception as exc:
            errors.append(f"{model}: {type(exc).__name__}")
    raise RuntimeError("Không model nào trả lời được: " + ", ".join(errors))


def test_model(spec: str) -> dict:
    """Gọi thử một model (trang quản trị)."""
    import time
    started = time.perf_counter()
    with track_usage() as usage:
        text = chat("Bạn là trợ lý kiểm tra kết nối.", "Trả lời đúng một từ: OK", model=spec, timeout=60,
                    max_tokens=64, effort="low")
    return {"ok": True, "reply": (text or "").strip()[:200], "latency_ms": int((time.perf_counter() - started) * 1000),
            "usage": usage}
