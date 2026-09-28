from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from src.services import search_srv
from src.core.runtime import run_blocking

router = APIRouter()

@router.get("/search_stream")
async def api_search(q: str = Query(..., min_length=1, max_length=4000), loai_vb: str = None, huong: str = None):
    gen = await run_blocking(search_srv.answer_stream, q, loai_vb=loai_vb, huong=huong)
    return StreamingResponse(gen, media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

@router.get("/search_agent_stream")
async def api_agent_search(q: str = Query(..., min_length=1, max_length=4000), loai_vb: str = None, huong: str = None):
    from src.agent import controller
    return StreamingResponse(
        await run_blocking(controller.answer_stream, q, loai_vb=loai_vb, huong=huong),
        media_type="text/event-stream",
    )
