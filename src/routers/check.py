from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from src.services import aggregate_srv

router = APIRouter()

@router.get("/aggregate")
async def api_aggregate(q: str = Query(...)):
    return JSONResponse(aggregate_srv.aggregate(q))