import base64
import logging
import os

import fitz  # PyMuPDF
import requests
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from src.core import config, runtime
from src.utils.uploads import remove_quietly, save_upload

logger = logging.getLogger(__name__)
router = APIRouter()

_IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


def _page_images(path: str) -> list[dict]:
    """Ảnh PNG (base64) của từng trang PDF, hoặc chính ảnh tải lên; giới hạn số trang."""
    extension = os.path.splitext(path)[1].lower()
    if extension in _IMAGE_TYPES:
        with open(path, "rb") as stream:
            data = base64.b64encode(stream.read()).decode("ascii")
        return [{"type": "image_url", "image_url": {"url": f"data:{_IMAGE_TYPES[extension]};base64,{data}"}}]
    images = []
    try:
        document = fitz.open(path)
    except Exception as exc:
        raise HTTPException(400, "Không đọc được tệp PDF.") from exc
    with document:
        if document.page_count > config.OCR_MAX_PAGES:
            raise HTTPException(413, f"Tệp có {document.page_count} trang, vượt giới hạn {config.OCR_MAX_PAGES} trang.")
        matrix = fitz.Matrix(config.OCR_DPI / 72, config.OCR_DPI / 72)
        for page in document:
            png = page.get_pixmap(matrix=matrix).tobytes("png")
            images.append({"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(png).decode("ascii")}})
    return images


def _ocr(path: str) -> str:
    images = _page_images(path)
    texts = []
    # Gửi theo lô vài trang để payload và bộ nhớ không tăng theo độ dài tài liệu.
    for start in range(0, len(images), config.OCR_PAGES_PER_REQUEST):
        batch = images[start:start + config.OCR_PAGES_PER_REQUEST]
        payload = {
            "model": "Unlimited-OCR",
            "messages": [{"role": "user", "content": [{"type": "text", "text": "Multi page parsing."}, *batch]}],
            "temperature": 0.0,
            "max_tokens": 16000,
        }
        response = requests.post(f"{config.OCR_SERVER_URL.rstrip('/')}/v1/chat/completions", json=payload,
                                 timeout=config.OCR_REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
        texts.append(response.json()["choices"][0]["message"]["content"])
    return "\n\n".join(texts)


@router.post("/process")
async def process_ocr(file: UploadFile = File(...)):
    path = await save_upload(file, {".pdf", *_IMAGE_TYPES}, config.UPLOAD_MAX_MB)
    try:
        return {"text": await runtime.run_blocking(_ocr, path, slots=runtime.UPLOAD_SLOTS)}
    except HTTPException:
        raise
    except requests.RequestException:
        logger.exception("Máy chủ OCR lỗi")
        return JSONResponse({"error": "Máy chủ OCR không phản hồi hoặc trả lỗi. Vui lòng thử lại sau."}, status_code=502)
    except Exception:
        logger.exception("OCR thất bại")
        return JSONResponse({"error": "Không OCR được tệp này."}, status_code=500)
    finally:
        remove_quietly(path)
