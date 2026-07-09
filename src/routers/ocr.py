import os
import tempfile
import base64
import json
import requests
import fitz  # PyMuPDF
from fastapi import APIRouter, UploadFile, File
from fastapi.responses import JSONResponse

router = APIRouter()

# Cấu hình địa chỉ SGLang Server chạy Unlimited-OCR
SGLANG_URL = os.getenv("OCR_SERVER_URL", "http://127.0.0.1:10000")

def encode_image(image_path: str) -> dict:
    ext = os.path.splitext(image_path)[1].lower()
    mime = "image/jpeg" if ext in (".jpg", ".jpeg") else f"image/{ext.lstrip('.')}"
    with open(image_path, "rb") as f:
        data = base64.b64encode(f.read()).decode("utf-8")
    return {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}}

@router.post("/process")
async def process_ocr(file: UploadFile = File(...)):
    suffix = os.path.splitext(file.filename)[1] or ".pdf"
    
    # 1. Lưu file PDF tạm
    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp_pdf:
        tmp_pdf.write(await file.read())
        pdf_path = tmp_pdf.name
        
    try:
        # 2. Chuyển PDF thành list ảnh (DPI = 300)
        doc = fitz.open(pdf_path)
        tmp_img_dir = tempfile.mkdtemp(prefix="pdf_ocr_")
        mat = fitz.Matrix(300 / 72, 300 / 72)
        
        image_paths = []
        for i, page in enumerate(doc):
            img_path = os.path.join(tmp_img_dir, f"page_{i + 1:04d}.png")
            page.get_pixmap(matrix=mat).save(img_path)
            image_paths.append(img_path)
        doc.close()
        
        # 3. Chuẩn bị payload gửi sang Unlimited-OCR
        content = [{"type": "text", "text": "Multi page parsing."}]
        for path in image_paths:
            content.append(encode_image(path))
            
        payload = {
            "model": "Unlimited-OCR",
            "messages": [{"role": "user", "content": content}],
            "temperature": 0.0,
            "max_tokens": 16000 # Giới hạn token trả về
        }

        # 4. Gửi Request tới SGLang
        response = requests.post(
            f"{SGLANG_URL}/v1/chat/completions",
            headers={"Content-Type": "application/json"},
            data=json.dumps(payload),
            timeout=1200 # Timeout cao do OCR chạy lâu
        )
        response.raise_for_status()
        
        res_data = response.json()
        ocr_text = res_data["choices"][0]["message"]["content"]
        
        return JSONResponse({"text": ocr_text})
        
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
        
    finally:
        # Dọn dẹp file tạm
        if os.path.exists(pdf_path):
            os.unlink(pdf_path)
        for p in image_paths:
            if os.path.exists(p):
                os.unlink(p)