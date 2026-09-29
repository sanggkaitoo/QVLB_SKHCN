from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from src.core import config, runtime
from src.services import aggregate_srv

router = APIRouter()


def _filters(loai_vb: str | None, huong: str | None, date_from: str | None, date_to: str | None) -> dict:
    return {"loai_vb": loai_vb, "huong": huong, "date_from": date_from, "date_to": date_to}


@router.get("/aggregate")
async def api_aggregate(q: str = Query(..., min_length=3, max_length=2000), loai_vb: str | None = None,
                        huong: str | None = None, date_from: str | None = None, date_to: str | None = None):
    return await runtime.run_blocking(aggregate_srv.aggregate, q, _filters(loai_vb, huong, date_from, date_to))


def _aggregate_stream(question: str, filters: dict):
    with runtime.budget(config.AGG_TIMEOUT_SECONDS):
        try:
            yield from aggregate_srv.aggregate_events(question, filters)
        except TimeoutError:
            yield {"type": "error", "message": "Tổng hợp quá thời gian cho phép. Hãy thu hẹp phạm vi và thử lại."}
        except Exception as exc:
            aggregate_srv.logger.exception("Aggregation stream failed")
            yield {"type": "error", "message": "Không hoàn tất được việc tổng hợp.", "detail": type(exc).__name__}


@router.get("/aggregate_stream")
async def api_aggregate_stream(q: str = Query(..., min_length=3, max_length=2000), loai_vb: str | None = None,
                               huong: str | None = None, date_from: str | None = None, date_to: str | None = None):
    """SSE: status (tiến độ) rồi aggregation_result."""
    events = runtime.open_event_stream(_aggregate_stream, q, _filters(loai_vb, huong, date_from, date_to))
    return StreamingResponse(runtime.sse_stream(events), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
