import os
import tempfile
from fastapi import APIRouter, UploadFile, File
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import google.generativeai as genai

from src.core import llm, config

router = APIRouter()

# Cấu hình khởi tạo API Key của Google
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

class SummarizeReq(BaseModel):
    text: str

@router.post("/transcribe")
async def api_transcribe_audio(file: UploadFile = File(...)):
    suffix = os.path.splitext(file.filename)[1] or ".mp3"
    
    # 1. Lưu file tạm xuống đĩa
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(await file.read())
        path = tmp.name
        
    try:
        # 2. Upload file âm thanh lên server của Gemini (File API)
        uploaded_audio = genai.upload_file(path=path)
        
        # 3. Dùng Gemini 2.5 Flash để bóc băng (Flash xử lý cực nhanh và rẻ)
        model = genai.GenerativeModel('gemini-2.5-flash')
        
        response = model.generate_content([
            "Hãy bóc băng (transcribe) chính xác toàn bộ nội dung file âm thanh này sang văn bản tiếng Việt. CHỈ trả về đoạn văn bản nội dung, tuyệt đối không bình luận, không giải thích hay thêm bất kỳ từ ngữ nào khác của bạn.",
            uploaded_audio
        ])
        
        # 4. Dọn dẹp: Xóa file âm thanh trên server Google ngay lập tức để bảo mật
        genai.delete_file(uploaded_audio.name)
        
        return JSONResponse({"text": response.text})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    finally:
        # Dọn dẹp file tạm trên ổ cứng của mình
        if os.path.exists(path):
            os.unlink(path)


# --- LUỒNG TÓM TẮT DÙNG GEMINI PRO ---
@router.post("/summarize")
async def api_summarize_audio(req: SummarizeReq):
    sys_prompt = """Bạn là trợ lý AI cấp cao chuyên trách thẩm định và xử lý văn bản hành chính từ băng ghi âm (transcript).
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
    
    try:
        # Vẫn sử dụng LLM_SMART (Gemini Pro) cấu hình qua OpenRouter để đảm bảo tính logic cao nhất
        summary = llm.chat(system=sys_prompt, user=req.text, model=config.LLM_SMART)
        return JSONResponse({"summary": summary})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)