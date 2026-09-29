"""LLM client (OpenAI-compatible: OpenRouter / Gemini OpenAI-compat).

Hàm chính:
  - chat(): trả text
  - chat_stream(): sinh từng đoạn text khi model trả về (stream thật)
  - extract_json(): ép model trả JSON, parse an toàn -> dict
"""
import json
import logging
import re
from contextlib import contextmanager
from contextvars import ContextVar
from openai import OpenAI
from src.core import config
from src.core.runtime import remaining

logger = logging.getLogger(__name__)

_usage_trace = ContextVar("llm_usage_trace", default=None)


@contextmanager
def track_usage():
    usage = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0, "responses_with_usage": 0}
    token = _usage_trace.set(usage)
    try:
        yield usage
    finally:
        _usage_trace.reset(token)


def _record_usage(raw_usage) -> None:
    usage = _usage_trace.get()
    if usage is None or raw_usage is None:
        return
    usage["prompt_tokens"] += getattr(raw_usage, "prompt_tokens", 0) or 0
    usage["completion_tokens"] += getattr(raw_usage, "completion_tokens", 0) or 0
    usage["responses_with_usage"] += 1


def _count_call() -> None:
    usage = _usage_trace.get()
    if usage is not None:
        usage["calls"] += 1


_client = OpenAI(
    base_url=config.LLM_BASE_URL,
    api_key=config.LLM_API_KEY,
    timeout=120.0,
    max_retries=3
)


def _messages(system: str, user: str) -> list[dict]:
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def chat(system: str, user: str, model: str | None = None,
         temperature: float = 0.1, timeout: float | None = None,
         max_tokens: int | None = None) -> str:
    model = model or config.LLM_MAIN
    request_client = _client.with_options(timeout=remaining(timeout or 120), max_retries=0)
    _count_call()
    resp = request_client.chat.completions.create(
        model=model,
        messages=_messages(system, user),
        temperature=temperature,
        max_tokens=min(max_tokens or config.LLM_MAX_OUTPUT_TOKENS, config.LLM_MAX_OUTPUT_TOKENS),
    )
    provider_error = getattr(resp, "error", None)
    if provider_error:
        raise RuntimeError(f"Provider returned an error payload: {str(provider_error)[:500]}")
    if not resp.choices:
        raise RuntimeError("Provider returned no completion choices")
    _record_usage(resp.usage)
    if resp.choices[0].finish_reason == "length":
        raise RuntimeError("Model output exceeded LLM_MAX_OUTPUT_TOKENS")
    return resp.choices[0].message.content


def chat_stream(system: str, user: str, models: list[str] | None = None,
                temperature: float = 0.1, timeout: float | None = None):
    """Yield text deltas. Falls back to the next model only if nothing was emitted yet."""
    candidates = list(dict.fromkeys(m for m in (models or [config.LLM_MAIN, config.LLM_FALLBACK]) if m))
    errors = []
    for model in candidates:
        emitted = False
        try:
            # httpx read timeout bounds silence between chunks, including the wait for the first token.
            idle = min(remaining(timeout or config.AGENT_TIMEOUT_SECONDS), config.LLM_STREAM_IDLE_TIMEOUT_SECONDS)
            request_client = _client.with_options(timeout=idle, max_retries=0)
            _count_call()
            stream = request_client.chat.completions.create(
                model=model,
                messages=_messages(system, user),
                temperature=temperature,
                max_tokens=config.LLM_MAX_OUTPUT_TOKENS,
                stream=True,
                stream_options={"include_usage": True},
            )
            finish_reason = None
            try:
                for chunk in stream:
                    remaining(config.AGENT_TIMEOUT_SECONDS)
                    provider_error = getattr(chunk, "error", None)
                    if provider_error:
                        raise RuntimeError(f"Provider returned an error payload: {str(provider_error)[:500]}")
                    if getattr(chunk, "usage", None):
                        _record_usage(chunk.usage)
                    if not chunk.choices:
                        continue
                    choice = chunk.choices[0]
                    finish_reason = choice.finish_reason or finish_reason
                    text = getattr(choice.delta, "content", None)
                    if text:
                        emitted = True
                        yield text
            finally:
                close = getattr(stream, "close", None)
                if close:
                    close()
            if finish_reason == "length":
                raise RuntimeError("Model output exceeded LLM_MAX_OUTPUT_TOKENS")
            if emitted:
                return
            errors.append(f"{model}: empty")
        except Exception as exc:
            if emitted:
                raise
            errors.append(f"{model}: {type(exc).__name__}")
            logger.warning("Streaming model %s failed: %s", model, exc)
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
        user=user, model=model or config.LLM_CHEAP, temperature=0.0, timeout=timeout,
        max_tokens=max_tokens,
    )
    return parse_json(raw)


def chat_with_fallback(system: str, user: str, models: list[str] | None = None,
                       temperature: float = 0.1) -> str:
    candidates = models or [config.LLM_MAIN, config.LLM_FALLBACK]
    errors = []
    for model in dict.fromkeys(candidate for candidate in candidates if candidate):
        try:
            answer = chat(system, user, model=model, temperature=temperature,
                          timeout=config.AGENT_TIMEOUT_SECONDS)
            if answer and answer.strip():
                return answer.strip()
        except Exception as exc:
            errors.append(f"{model}: {type(exc).__name__}")
    raise RuntimeError("Không model nào trả lời được: " + ", ".join(errors))
