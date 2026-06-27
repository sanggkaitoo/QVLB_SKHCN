from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from src.services import search_srv

router = APIRouter()

@router.get("/search_stream")
async def api_search(q: str = Query(...), loai_vb: str = None, huong: str = None):
    gen = search_srv.answer_stream(q, loai_vb=loai_vb, huong=huong)
    return StreamingResponse(gen, media_type="text/event-stream")