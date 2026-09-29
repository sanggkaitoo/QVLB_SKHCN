from fastapi import APIRouter, File, UploadFile

from src.check import content_check, format_check
from src.core import config, runtime
from src.utils.uploads import remove_quietly, save_upload

router = APIRouter()


@router.post("/format")
async def api_check_format(file: UploadFile = File(...)):
    path = await save_upload(file, {".docx"}, config.UPLOAD_MAX_MB)
    try:
        return await runtime.run_blocking(format_check.check_format, path, slots=runtime.UPLOAD_SLOTS)
    finally:
        remove_quietly(path)


@router.post("/content")
async def api_check_content(file: UploadFile = File(...)):
    path = await save_upload(file, {".docx", ".doc", ".pdf"}, config.UPLOAD_MAX_MB)
    try:
        return await runtime.run_blocking(content_check.check_content, path, file.filename, slots=runtime.UPLOAD_SLOTS)
    finally:
        remove_quietly(path)
