import asyncio

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from src.agent import controller
from src.core import app_settings, runtime, security
from src.core.security import User
from src.services import model_catalog

router = APIRouter()

_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def _filters(loai_vb: str | None, huong: str | None, linh_vuc: str | None) -> dict:
    # Giá trị nhiều lựa chọn gửi dạng "a,b"; document_fields.normalize_filters kiểm tra hợp lệ.
    return {"loai_vb": loai_vb, "huong": huong, "linh_vuc": linh_vuc}


_searcher = security.require("tools.search", allow_anonymous_setting="access.anonymous_search")


def _answer_model(user: User | None, requested: str | None) -> str | None:
    """Model trả lời: lựa chọn của người dùng (nếu được phép và admin đã bật) hoặc mặc định của admin."""
    if requested and user and user.can("models.choose") and model_catalog.is_user_choice(requested):
        return requested
    return app_settings.get("ai.default_model") or None


async def _prepare(user: User | None, model: str | None) -> dict:
    await asyncio.to_thread(security.check_quota, user)
    return {"user_id": user.id if user else None,
            "answer_model": await asyncio.to_thread(_answer_model, user, model)}


@router.get("/search_stream")
async def api_search(q: str = Query(..., min_length=1, max_length=4000), loai_vb: str | None = None,
                     huong: str | None = None, linh_vuc: str | None = None,
                     model: str | None = Query(None, max_length=200), user: User | None = Depends(_searcher)):
    """Server-Sent Events: status, sources, token (bản nháp), answer (bản đã kiểm chứng), done."""
    events = runtime.open_event_stream(controller.agent_events, q, filters=_filters(loai_vb, huong, linh_vuc),
                                       **await _prepare(user, model))
    return StreamingResponse(runtime.sse_stream(events), media_type="text/event-stream", headers=_SSE_HEADERS)


# Alias giữ tương thích; dùng cùng cấu hình định tuyến.
router.add_api_route("/search_agent_stream", api_search, methods=["GET"])


@router.get("/search")
async def api_search_json(q: str = Query(..., min_length=1, max_length=4000), loai_vb: str | None = None,
                          huong: str | None = None, linh_vuc: str | None = None,
                          model: str | None = Query(None, max_length=200), user: User | None = Depends(_searcher)):
    """Kết quả hoàn chỉnh (không stream) cho tích hợp và đo đánh giá."""
    result = await runtime.run_blocking(controller.run_agent, q, filters=_filters(loai_vb, huong, linh_vuc),
                                        **await _prepare(user, model))
    return result.model_dump(mode="json")
