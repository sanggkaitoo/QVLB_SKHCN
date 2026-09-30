import base64
import logging
import os

import fitz  # PyMuPDF
import requests
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from src.core import config, multimodal, runtime
from src.utils.uploads import remove_quietly, save_upload

logger = logging.getLogger(__name__)
router = APIRouter()

_IMAGE_TYPES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


_OCR_PROMPT = ("Nhận dạng chính xác toàn bộ chữ trong các trang ảnh sau (văn bản hành chính tiếng Việt). "
               "Giữ nguyên thứ tự, xuống dòng và bảng biểu (dùng bảng Markdown). "
               "CHỈ trả về nội dung nhận dạng, không bình luận.")


def _page_images(path: str) -> list[tuple[str, str]]:
    """Ảnh (mime, base64) của từng trang PDF, hoặc chính ảnh tải lên; giới hạn số trang."""
    extension = os.path.splitext(path)[1].lower()
    if extension in _IMAGE_TYPES:
        with open(path, "rb") as stream:
            return [(_IMAGE_TYPES[extension], base64.b64encode(stream.read()).decode("ascii"))]
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
            images.append(("image/png", base64.b64encode(png).decode("ascii")))
    return images


def _ocr(path: str) -> str:
    images = _page_images(path)
    texts = []
    # Gửi theo lô vài trang để payload và bộ nhớ không tăng theo độ dài tài liệu.
    for start in range(0, len(images), config.OCR_PAGES_PER_REQUEST):
        batch = images[start:start + config.OCR_PAGES_PER_REQUEST]
        texts.append(multimodal.ocr_batch(_OCR_PROMPT, batch))
    return "\n\n".join(texts)


@router.post("/process")
async def process_ocr(file: UploadFile = File(...)):
    path = await save_upload(file, {".pdf", *_IMAGE_TYPES}, config.UPLOAD_MAX_MB)
    try:
        return {"text": await runtime.run_blocking(_ocr, path, slots=runtime.UPLOAD_SLOTS)}
    except HTTPException:
        raise
    except multimodal.UnsupportedInput as exc:
        return JSONResponse({"error": str(exc)}, status_code=422)
    except requests.RequestException:
        logger.exception("Máy chủ OCR lỗi")
        return JSONResponse({"error": "Máy chủ OCR không phản hồi hoặc trả lỗi. Vui lòng thử lại sau."}, status_code=502)
    except Exception:
        logger.exception("OCR thất bại")
        return JSONResponse({"error": "Không OCR được tệp này."}, status_code=500)
    finally:
        remove_quietly(path)
