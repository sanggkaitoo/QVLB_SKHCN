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
         temperature: float = 0.1, stream: bool = False):
    model = model or config.LLM_MAIN
    resp = _client.chat.completions.create(
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


def extract_json(system: str, user: str, model: str | None = None) -> dict | list | None:
    """Gọi model với yêu cầu CHỈ trả JSON; bóc tách an toàn."""
    model = model or config.LLM_CHEAP
    raw = chat(
        system=system + "\n\nCHỈ trả về JSON hợp lệ, không kèm giải thích, không markdown.",
        user=user, model=model, temperature=0.0,
    )
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
