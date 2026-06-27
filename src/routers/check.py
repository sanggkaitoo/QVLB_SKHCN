import os
import tempfile
from fastapi import APIRouter, UploadFile, File
from fastapi.responses import JSONResponse
from src.check import format_check, content_check

router = APIRouter()

@router.post("/format")
async def api_check_format(file: UploadFile = File(...)):
    suffix = os.path.splitext(file.filename)[1] or ".docx"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await file.read())
        path = tmp.name
    try:
        return JSONResponse(format_check.check_format(path))
    finally:
        os.unlink(path)

@router.post("/content")
async def api_check_content(file: UploadFile = File(...)):
    suffix = os.path.splitext(file.filename)[1] or ".docx"
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await file.read())
        path = tmp.name
    try:
        return JSONResponse(content_check.check_content(path))
    finally:
        os.unlink(path)