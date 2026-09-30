"""Cấu hình AI trên trang quản trị và danh sách model công khai (bảng giá, chọn model khi tra cứu)."""
import asyncio
import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

from src.core import anthropic_provider, app_settings, llm, security
from src.core.security import User
from src.services import model_catalog

logger = logging.getLogger(__name__)
router = APIRouter()          # /api/admin/ai
public_router = APIRouter()   # /api/models
Provider = Literal["openrouter", "openai", "anthropic", "gemini"]
_viewer = security.require("settings.view", "settings.manage")
_manager = security.require("settings.manage")

# Model gọi trực tiếp gợi ý sẵn (danh mục đầy đủ lấy từ API của nhà cung cấp khi đã có khoá).
_GEMINI_DIRECT = ["gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.5-flash-lite"]
_OPENAI_AUDIO = ["gpt-4o-transcribe", "gpt-4o-mini-transcribe", "whisper-1"]


class ModelEntry(BaseModel):
    spec: str = Field(..., min_length=3, max_length=200)
    featured: bool = False
    selectable: bool = False
    note: str | None = Field(None, max_length=120)


class ModelsRequest(BaseModel):
    models: list[ModelEntry] = Field(default_factory=list, max_length=60)
    default_model: str | None = Field(None, max_length=200)


class KeyRequest(BaseModel):
    api_key: str | None = Field(None, max_length=500)


class TestRequest(BaseModel):
    spec: str = Field(..., min_length=3, max_length=200)


class AccessRequest(BaseModel):
    anonymous_search: bool


def _task_rows() -> list[dict]:
    rows = []
    overrides = app_settings.get("ai.roles") or {}
    for task, meta in llm.TASKS.items():
        spec = llm.resolve("@" + task)
        ok, reason = model_catalog.supports(spec, task)
        rows.append({"task": task, "label": meta["label"], "inputs": meta["inputs"], "spec": spec,
                     "source": "admin" if overrides.get(task) else "env", "model": model_catalog.describe(spec),
                     "compatible": ok, "problem": reason})
    return rows


def _providers() -> list[dict]:
    output = []
    for provider, label in llm.PROVIDERS.items():
        if provider == "local":
            continue
        source = llm.key_source(provider)
        output.append({"provider": provider, "label": label, "configured": bool(source), "source": source,
                       "masked": app_settings.mask(llm.provider_key(provider)) if source else None})
    return output


@router.get("/overview")
async def api_ai_overview(_: User = Depends(_viewer)):
    def build():
        model_catalog.openrouter_catalog()
        return {"providers": _providers(), "tasks": _task_rows(), "models": model_catalog.enabled_models(),
                "default_model": app_settings.get("ai.default_model"),
                "anonymous_search": bool(app_settings.get("access.anonymous_search", False)),
                "input_labels": model_catalog.INPUT_LABELS, "catalog_age": model_catalog.catalog_age(),
                "question_tokens": [model_catalog.QUESTION_PROMPT_TOKENS, model_catalog.QUESTION_COMPLETION_TOKENS]}
    return await asyncio.to_thread(build)


def _direct_models(provider: str) -> list[dict]:
    key = llm.provider_key(provider)
    ids: list[str] = []
    if provider == "anthropic":
        try:
            ids = [item["id"] for item in anthropic_provider.list_models(key)] if key else []
        except Exception as exc:
            logger.warning("Không lấy được danh mục Claude: %s", exc)
        ids = ids or [item["id"] for item in anthropic_provider.KNOWN_MODELS]
    elif provider == "openai":
        if key:
            try:
                listed = llm._openai_client("openai").with_options(timeout=15).models.list()
                ids = sorted(item.id for item in listed if item.id.startswith(("gpt-", "o3", "o4", "whisper")))
                ids = [item for item in ids if not any(skip in item for skip in ("realtime", "tts", "image", "search"))]
            except Exception as exc:
                logger.warning("Không lấy được danh mục OpenAI: %s", exc)
        if not ids:
            ids = [item["id"].split("/", 1)[1] for item in model_catalog.openrouter_catalog()
                   if item["vendor_key"] == "openai" and not item["variant"]][:25] + _OPENAI_AUDIO
    elif provider == "gemini":
        ids = _GEMINI_DIRECT
    return [model_catalog.describe(f"{provider}:{model_id}") for model_id in ids]


@router.get("/catalog")
async def api_ai_catalog(provider: Literal["openrouter", "openai", "anthropic", "gemini", "local"] = "openrouter",
                         q: str = Query("", max_length=100), input: str | None = Query(None, max_length=20),
                         refresh: bool = False, limit: int = Query(80, ge=1, le=500),
                         _: User = Depends(_viewer)):
    def build():
        if provider == "openrouter":
            items = [item for item in model_catalog.openrouter_catalog(refresh=refresh) if not item["variant"]]
        elif provider == "local":
            items = [model_catalog.describe("local:Unlimited-OCR")]
        else:
            items = _direct_models(provider)
        needle = q.strip().lower()
        if needle:
            items = [item for item in items if needle in item["spec"].lower() or needle in (item["name"] or "").lower()]
        if input:
            items = [item for item in items if input in item["inputs"]]
        items = sorted(items, key=lambda item: -(item.get("created") or 0))
        return {"items": [{**item, "question_vnd": model_catalog.question_cost_vnd(item),
                           "tasks": [task for task in llm.TASKS if model_catalog.supports(item["spec"], task)[0]]}
                          for item in items[:limit]], "total": len(items)}
    return await asyncio.to_thread(build)


@router.put("/models")
async def api_ai_models(req: ModelsRequest, request: Request, user: User = Depends(_manager)):
    entries = list({entry.spec: entry.model_dump() for entry in req.models}.values())
    if req.default_model and req.default_model not in {entry["spec"] for entry in entries if entry["selectable"]}:
        raise HTTPException(400, "Model mặc định phải nằm trong danh sách người dùng được chọn.")
    app_settings.put("ai.models", entries, user.id)
    app_settings.put("ai.default_model", req.default_model, user.id)
    security.audit(user, "ai_models_saved", None, {"count": len(entries)}, security.client_ip(request))
    return {"ok": True}


@router.put("/tasks")
async def api_ai_tasks(assignments: dict[str, str | None], request: Request, user: User = Depends(_manager)):
    roles = dict(app_settings.get("ai.roles") or {})
    for task, spec in assignments.items():
        if task not in llm.TASKS:
            raise HTTPException(400, f"Không có tính năng {task}.")
        if not spec:
            roles.pop(task, None)  # quay về mặc định trong .env
            continue
        ok, reason = await asyncio.to_thread(model_catalog.supports, spec, task)
        if not ok:
            raise HTTPException(400, f"{llm.TASKS[task]['label']}: {reason}")
        roles[task] = spec
    app_settings.put("ai.roles", roles, user.id)
    security.audit(user, "ai_tasks_saved", None, roles, security.client_ip(request))
    return {"ok": True, "tasks": await asyncio.to_thread(_task_rows)}


@router.put("/keys/{provider}")
async def api_ai_key(provider: Provider, req: KeyRequest, request: Request, user: User = Depends(_manager)):
    await asyncio.to_thread(app_settings.set_api_key, provider, (req.api_key or "").strip() or None, user.id)
    security.audit(user, "ai_key_saved" if req.api_key else "ai_key_removed", provider, None, security.client_ip(request))
    return {"ok": True, "providers": _providers()}


@router.post("/test")
async def api_ai_test(req: TestRequest, _: User = Depends(_manager)):
    provider, _model = llm.split_spec(req.spec)
    if provider == "local":
        return {"ok": True, "reply": "Máy chủ OCR nội bộ được kiểm tra khi chạy OCR.", "latency_ms": 0}
    if model_catalog.describe(req.spec)["inputs"] == ["audio"]:
        return {"ok": True, "reply": "Model gỡ băng chỉ nhận âm thanh; hãy thử bằng tính năng gỡ băng.", "latency_ms": 0}
    try:
        return await asyncio.to_thread(llm.test_model, req.spec)
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {str(exc)[:300]}"}


@router.put("/access")
async def api_ai_access(req: AccessRequest, request: Request, user: User = Depends(_manager)):
    app_settings.put("access.anonymous_search", req.anonymous_search, user.id)
    security.audit(user, "access_changed", None, req.model_dump(), security.client_ip(request))
    return {"ok": True}


# ------------------------------------------------------------------ công khai

@public_router.get("/featured")
async def api_models_featured():
    """Bảng giá trên trang tổng quan: model nổi bật admin chọn, giá theo danh mục OpenRouter."""
    items = await asyncio.to_thread(model_catalog.featured)
    return {"items": items, "question_tokens": [model_catalog.QUESTION_PROMPT_TOKENS,
                                                 model_catalog.QUESTION_COMPLETION_TOKENS],
            "usd_to_vnd": model_catalog.config.USD_TO_VND}


@public_router.get("/choices")
async def api_models_choices(request: Request):
    """Model người dùng hiện tại được chọn khi tra cứu."""
    user = await asyncio.to_thread(security.current_user, request)
    if not user or not user.can("models.choose"):
        return {"items": [], "default": None, "can_choose": False}
    return {"items": await asyncio.to_thread(model_catalog.user_choices),
            "default": app_settings.get("ai.default_model"), "can_choose": True}
