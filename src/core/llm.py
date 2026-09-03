"""LLM client (OpenAI-compatible: OpenRouter / Gemini OpenAI-compat).

Hai hàm chính:
  - chat(): trả text (có thể stream)
  - extract_json(): ép model trả JSON, parse an toàn -> dict
"""
import json
import re
from openai import OpenAI
from src.core import config

_client = OpenAI(
    base_url=config.LLM_BASE_URL,
    api_key=config.LLM_API_KEY,
    timeout=120.0,
    max_retries=3
)


def chat(system: str, user: str, model: str | None = None,
         temperature: float = 0.1, stream: bool = False,
         timeout: float | None = None):
    model = model or config.LLM_MAIN
    request_client = _client.with_options(timeout=timeout, max_retries=0) if timeout else _client
    resp = request_client.chat.completions.create(
        model=model,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
        temperature=temperature,
        stream=stream,
    )
    if stream:
        return resp  # caller iterates chunks
    return resp.choices[0].message.content


_JSON_RE = re.compile(r"\{.*\}|\[.*\]", re.DOTALL)


def extract_json(system: str, user: str, model: str | None = None,
                 timeout: float | None = None) -> dict | list | None:
    """Gọi model với yêu cầu CHỈ trả JSON; bóc tách an toàn."""
    model = model or config.LLM_CHEAP
    raw = chat(
        system=system + "\n\nCHỈ trả về JSON hợp lệ, không kèm giải thích, không markdown.",
        user=user, model=model, temperature=0.0, timeout=timeout,
    )
    if not raw:
        return None
    raw = raw.strip()
    raw = re.sub(r"^```(json)?|```$", "", raw, flags=re.MULTILINE).strip()
    try:
        return json.loads(raw)
    except Exception:
        m = _JSON_RE.search(raw)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                return None
    return None


def chat_with_fallback(system: str, user: str, models: list[str] | None = None,
                       temperature: float = 0.1) -> str:
    candidates = models or [config.LLM_MAIN, config.LLM_FALLBACK]
    errors = []
    for model in dict.fromkeys(candidate for candidate in candidates if candidate):
        try:
            answer = chat(
                system, user, model=model, temperature=temperature, stream=False,
                timeout=config.AGENT_TIMEOUT_SECONDS,
            )
            if answer and answer.strip():
                return answer.strip()
        except Exception as exc:
            errors.append(f"{model}: {type(exc).__name__}")
    raise RuntimeError("Không model nào trả lời được: " + ", ".join(errors))