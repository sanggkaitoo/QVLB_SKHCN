from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from src.services import aggregate_srv
from src.core.runtime import run_blocking

router = APIRouter()

@router.get("/aggregate")
async def api_aggregate(q: str = Query(...)):
    return JSONResponse(await run_blocking(aggregate_srv.aggregate, q))
