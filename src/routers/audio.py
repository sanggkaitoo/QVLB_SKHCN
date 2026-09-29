import logging
import os

import google.generativeai as genai
from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from src.core import config, llm, runtime
from src.utils.uploads import remove_quietly, save_upload

logger = logging.getLogger(__name__)
router = APIRouter()

_AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".webm", ".mp4"}
_TRANSCRIBE_PROMPT = ("Hãy bóc băng (transcribe) chính xác toàn bộ nội dung file âm thanh này sang văn bản tiếng Việt. "
                      "CHỈ trả về đoạn văn bản nội dung, tuyệt đối không bình luận, không giải thích hay thêm bất kỳ từ ngữ nào khác của bạn.")

_SUMMARY_PROMPT = """Bạn là trợ lý AI cấp cao chuyên trách thẩm định và xử lý văn bản hành chính từ băng ghi âm (transcript).
Văn bản gỡ băng gốc thường lủng củng, có độ nhiễu cao, sai chính tả do nhận diện âm thanh, từ ngữ lặp hoặc ngập ngừng.

NHIỆM VỤ CỦA BẠN:
1. Lọc bỏ toàn bộ từ ngữ thừa, từ đệm, văn phong nói lặp lại hoặc các đoạn ngập ngừng.
2. Hiệu chỉnh thông minh các lỗi chính tả, thuật ngữ chuyên ngành hành chính.
3. Cấu trúc lại toàn bộ nội dung thành một bản ghi chú (Note) mạch lạc, nghiêm túc và chuyên nghiệp.
4. Trình bày nội dung rõ ràng bằng định dạng Markdown gồm các phần:
   - **Chủ đề chính:** Tóm tắt bối cảnh hoặc mục đích cốt lõi.
   - **Nội dung trọng tâm:** Sử dụng các danh sách đầu dòng (bullet points) để phân rã các ý chính.
   - **Kết luận / Phân công:** Ghi rõ mốc thời gian, công việc và trách nhiệm (nếu có).
5. Tuyệt đối trung thành với thông tin gốc, KHÔNG bịa đặt thêm số liệu."""

_gemini_configured = False


class SummarizeReq(BaseModel):
    text: str = Field(..., min_length=1, max_length=200_000)


def _transcribe(path: str) -> str:
    global _gemini_configured
    api_key = os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise HTTPException(503, "Chức năng gỡ băng chưa được cấu hình (thiếu GEMINI_API_KEY).")
    if not _gemini_configured:
        genai.configure(api_key=api_key)
        _gemini_configured = True
    uploaded = genai.upload_file(path=path)
    try:
        model = genai.GenerativeModel("gemini-2.5-flash")
        return model.generate_content([_TRANSCRIBE_PROMPT, uploaded], request_options={"timeout": 600}).text
    finally:
        # Xóa tệp trên máy chủ Google cả khi gỡ băng lỗi.
        try:
            genai.delete_file(uploaded.name)
        except Exception as exc:
            logger.warning("Không xóa được tệp âm thanh trên Gemini: %s", exc)


@router.post("/transcribe")
async def api_transcribe_audio(file: UploadFile = File(...)):
    path = await save_upload(file, _AUDIO_EXTENSIONS, config.AUDIO_MAX_MB)
    try:
        text = await runtime.run_blocking(_transcribe, path, slots=runtime.UPLOAD_SLOTS)
        return {"text": text}
    except HTTPException:
        raise
    except Exception:
        logger.exception("Gỡ băng thất bại")
        return JSONResponse({"error": "Không gỡ băng được tệp âm thanh. Vui lòng thử lại sau."}, status_code=502)
    finally:
        remove_quietly(path)


@router.post("/summarize")
async def api_summarize_audio(req: SummarizeReq):
    try:
        summary = await runtime.run_blocking(llm.chat, _SUMMARY_PROMPT, req.text, model=config.LLM_SMART,
                                             slots=runtime.UPLOAD_SLOTS)
        return {"summary": summary}
    except HTTPException:
        raise
    except Exception:
        logger.exception("Tóm tắt băng ghi âm thất bại")
        return JSONResponse({"error": "Không tóm tắt được nội dung. Vui lòng thử lại sau."}, status_code=502)
