"""Danh mục model AI: giá, ngữ cảnh và loại đầu vào (text/ảnh/âm thanh/tệp) của từng model.

Nguồn chính là API công khai của OpenRouter (/api/v1/models, không cần khoá). Model gọi trực tiếp
qua OpenAI / Anthropic / Gemini lấy giá và loại đầu vào từ bản tương ứng trên OpenRouter
(cùng model, cùng giá niêm yết), nếu không có thì dùng quy tắc tĩnh.
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
from pathlib import Path

import requests

from src.core import app_settings, config, llm

logger = logging.getLogger(__name__)

OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
_CACHE_FILE = Path("data/openrouter_models.json")
_CACHE_SECONDS = 6 * 3600
_lock = threading.Lock()
_catalog: list[dict] = []
_catalog_at = 0.0

# Ước tính chi phí một câu hỏi tra cứu: ngữ cảnh bằng chứng ~6.000 token vào, câu trả lời ~700 token ra.
QUESTION_PROMPT_TOKENS = 6000
QUESTION_COMPLETION_TOKENS = 700
_VENDORS = {"openai": "OpenAI", "anthropic": "Anthropic", "google": "Google", "qwen": "Alibaba Qwen",
            "deepseek": "DeepSeek", "meta-llama": "Meta", "mistralai": "Mistral", "x-ai": "xAI",
            "moonshotai": "Moonshot", "z-ai": "Zhipu", "minimax": "MiniMax", "xiaomi": "Xiaomi"}
_FEATURED_VENDORS = ("openai", "anthropic", "google", "qwen", "deepseek", "x-ai")
INPUT_LABELS = {"text": "Văn bản", "image": "Ảnh", "audio": "Âm thanh", "file": "Tệp PDF", "video": "Video"}


def _price(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return round(number * 1_000_000, 4) if number >= 0 else None  # USD / 1 triệu token


def _normalize(raw: dict) -> dict | None:
    architecture = raw.get("architecture") or {}
    outputs = architecture.get("output_modalities") or ["text"]
    if "text" not in outputs:  # model sinh ảnh/âm thanh không dùng cho các tính năng ở đây
        return None
    model_id = raw["id"]
    vendor = model_id.split("/", 1)[0]
    pricing = raw.get("pricing") or {}
    name = raw.get("name") or model_id
    return {
        "spec": model_id,
        "provider": "openrouter",
        "id": model_id,
        "name": re.sub(r"^[^:]{1,40}:\s*", "", name),
        "vendor": _VENDORS.get(vendor, vendor),
        "vendor_key": vendor,
        "context": raw.get("context_length"),
        "prompt": _price(pricing.get("prompt")),
        "completion": _price(pricing.get("completion")),
        "inputs": sorted(set(architecture.get("input_modalities") or ["text"])),
        "created": raw.get("created") or 0,
        "free": model_id.endswith(":free"),
        "variant": ":" in model_id or model_id.startswith("~") or vendor == "openrouter",
        "description": (raw.get("description") or "")[:280],
    }


def _download() -> list[dict]:
    response = requests.get(OPENROUTER_MODELS_URL, timeout=20)
    response.raise_for_status()
    return [item for item in (_normalize(raw) for raw in response.json().get("data", [])) if item]


def openrouter_catalog(refresh: bool = False) -> list[dict]:
    global _catalog, _catalog_at
    with _lock:
        if _catalog and not refresh and time.time() - _catalog_at < _CACHE_SECONDS:
            return _catalog
    try:
        catalog = _download()
        _CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_FILE.write_text(json.dumps({"at": time.time(), "models": catalog}, ensure_ascii=False))
        fetched_at = time.time()
    except Exception as exc:
        logger.warning("Không tải được danh mục OpenRouter: %s", exc)
        if _catalog:
            return _catalog
        try:
            cached = json.loads(_CACHE_FILE.read_text())
            catalog, fetched_at = cached["models"], cached["at"]
        except Exception:
            return []
    with _lock:
        _catalog, _catalog_at = catalog, fetched_at
    return catalog


def catalog_age() -> float | None:
    return time.time() - _catalog_at if _catalog_at else None


def _by_id() -> dict[str, dict]:
    return {item["id"]: item for item in openrouter_catalog()}


def _openrouter_twin(provider: str, model_id: str) -> dict | None:
    """Bản tương ứng trên OpenRouter của model gọi trực tiếp (để lấy giá và loại đầu vào)."""
    vendor = {"openai": "openai", "anthropic": "anthropic", "gemini": "google"}.get(provider)
    if not vendor:
        return None
    catalog = _by_id()
    base = re.sub(r"-\d{8}$", "", model_id)  # bỏ hậu tố ngày: claude-haiku-4-5-20251001
    for candidate in (base, re.sub(r"(\d)-(\d)", r"\1.\2", base)):
        if f"{vendor}/{candidate}" in catalog:
            return catalog[f"{vendor}/{candidate}"]
    return None


_STATIC_INPUTS = {
    "anthropic": ["file", "image", "text"],
    "gemini": ["audio", "file", "image", "text", "video"],
    "local": ["image"],
}


def describe(spec: str) -> dict:
    """Thông tin một model bất kỳ theo spec "nhà_cung_cấp:model"."""
    provider, model_id = llm.split_spec(spec)
    if provider == "openrouter":
        found = _by_id().get(model_id)
        if found:
            return {**found, "spec": spec}
        return {"spec": spec, "provider": provider, "id": model_id, "name": model_id, "vendor": model_id.split("/")[0],
                "inputs": ["text"], "prompt": None, "completion": None, "context": None, "unknown": True}
    twin = _openrouter_twin(provider, model_id) or {}
    if provider == "openai":
        if "transcribe" in model_id or model_id.startswith("whisper"):
            inputs = ["audio"]
        elif "audio" in model_id:
            inputs = ["audio", "text"]
        else:
            inputs = twin.get("inputs") or ["text"]
    else:
        inputs = _STATIC_INPUTS.get(provider) or twin.get("inputs") or ["text"]
    return {"spec": spec, "provider": provider, "id": model_id,
            "name": twin.get("name") or model_id, "vendor": llm.PROVIDERS.get(provider, provider),
            "inputs": sorted(set(inputs)), "prompt": 0.0 if provider == "local" else twin.get("prompt"),
            "completion": 0.0 if provider == "local" else twin.get("completion"),
            "context": twin.get("context"), "unknown": not twin and provider != "local"}


def supports(spec: str, task: str) -> tuple[bool, str | None]:
    """Model có nhận được loại dữ liệu tính năng cần không (ví dụ gỡ băng cần âm thanh)."""
    info = describe(spec)
    needed = llm.TASKS[task]["inputs"]
    missing = [kind for kind in needed if kind not in info["inputs"]]
    if missing:
        labels = ", ".join(INPUT_LABELS.get(kind, kind) for kind in missing)
        return False, f"Model {info['name']} không nhận đầu vào loại: {labels}."
    provider, model_id = llm.split_spec(spec)
    if task != "transcribe" and provider == "openai" and ("transcribe" in model_id or model_id.startswith("whisper")):
        return False, "Model chuyển giọng nói thành văn bản chỉ dùng cho tính năng gỡ băng."
    if provider == "local" and task != "ocr":
        return False, "Máy chủ OCR nội bộ chỉ dùng cho tính năng OCR."
    if task == "transcribe" and provider == "anthropic":
        return False, "Claude chưa nhận âm thanh."
    return True, None


def question_cost_vnd(info: dict) -> int | None:
    if info.get("prompt") is None or info.get("completion") is None:
        return None
    usd = (QUESTION_PROMPT_TOKENS * info["prompt"] + QUESTION_COMPLETION_TOKENS * info["completion"]) / 1_000_000
    return round(usd * config.USD_TO_VND)


def enabled_models() -> list[dict]:
    """Model admin đã bật, kèm giá hiện hành."""
    output = []
    for entry in app_settings.get("ai.models") or []:
        info = describe(entry["spec"])
        output.append({**info, "featured": bool(entry.get("featured")), "selectable": bool(entry.get("selectable")),
                       "note": entry.get("note") or "", "question_vnd": question_cost_vnd(info)})
    return output


def assignments() -> dict[str, str]:
    return {task: llm.resolve("@" + task) for task in llm.TASKS}


def _auto_featured() -> list[dict]:
    """Khi admin chưa chọn: model đang dùng + model mới nhất của các hãng lớn trên OpenRouter."""
    picked: dict[str, dict] = {}
    for task in ("answer", "check", "transcribe"):
        spec = llm.resolve("@" + task)
        if llm.split_spec(spec)[0] != "local":
            picked.setdefault(spec, describe(spec))
    newest: dict[str, dict] = {}
    for item in openrouter_catalog():
        if item["variant"] or item["prompt"] is None or item["vendor_key"] not in _FEATURED_VENDORS:
            continue
        if "pro" in item["id"].split("/")[-1].split("-") and item["vendor_key"] == "openai":
            continue  # bản "pro" của OpenAI rất đắt, không tiêu biểu cho sử dụng thường ngày
        current = newest.get(item["vendor_key"])
        if current is None or item["created"] > current["created"]:
            newest[item["vendor_key"]] = item
    for item in sorted(newest.values(), key=lambda value: -value["created"]):
        picked.setdefault(item["spec"], item)
    return list(picked.values())[:6]


def featured() -> list[dict]:
    chosen = [item for item in enabled_models() if item["featured"]]
    items = chosen or [{**item, "question_vnd": question_cost_vnd(item)} for item in _auto_featured()]
    in_use = {}
    for task, spec in assignments().items():
        in_use.setdefault(spec, []).append(llm.TASKS[task]["label"].split(" – ")[0])
    output = []
    for item in items:
        output.append({key: item.get(key) for key in ("spec", "name", "vendor", "provider", "context", "prompt",
                                                       "completion", "inputs", "question_vnd", "note")}
                      | {"used_for": sorted(set(in_use.get(item["spec"], [])))})
    return output


def user_choices() -> list[dict]:
    """Model người dùng được chọn khi tra cứu (chỉ model nhận văn bản)."""
    choices = [item for item in enabled_models() if item["selectable"] and supports(item["spec"], "answer")[0]]
    return [{"spec": item["spec"], "name": item["name"], "vendor": item["vendor"], "question_vnd": item["question_vnd"]}
            for item in choices]


def is_user_choice(spec: str) -> bool:
    return any(item["spec"] == spec for item in user_choices())
