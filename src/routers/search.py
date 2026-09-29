from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from src.agent import controller
from src.core import runtime

router = APIRouter()

_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


def _filters(loai_vb: str | None, huong: str | None, linh_vuc: str | None) -> dict:
    # Giá trị nhiều lựa chọn gửi dạng "a,b"; document_fields.normalize_filters kiểm tra hợp lệ.
    return {"loai_vb": loai_vb, "huong": huong, "linh_vuc": linh_vuc}


@router.get("/search_stream")
async def api_search(q: str = Query(..., min_length=1, max_length=4000), loai_vb: str | None = None,
                     huong: str | None = None, linh_vuc: str | None = None):
    """Server-Sent Events: status, sources, token (bản nháp), answer (bản đã kiểm chứng), done."""
    events = runtime.open_event_stream(controller.agent_events, q, filters=_filters(loai_vb, huong, linh_vuc))
    return StreamingResponse(runtime.sse_stream(events), media_type="text/event-stream", headers=_SSE_HEADERS)


# Alias giữ tương thích; dùng cùng cấu hình định tuyến.
router.add_api_route("/search_agent_stream", api_search, methods=["GET"])


@router.get("/search")
async def api_search_json(q: str = Query(..., min_length=1, max_length=4000), loai_vb: str | None = None,
                          huong: str | None = None, linh_vuc: str | None = None):
    """Kết quả hoàn chỉnh (không stream) cho tích hợp và đo đánh giá."""
    result = await runtime.run_blocking(controller.run_agent, q, filters=_filters(loai_vb, huong, linh_vuc))
    return result.model_dump(mode="json")
