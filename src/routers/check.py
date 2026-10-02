import json
import unicodedata

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from src.check import content_check, content_review, docx_check, docx_rules, format_check, legal_registry
from src.core import app_settings, config, llm, runtime, security
from src.core.security import User
from src.utils.uploads import remove_quietly, save_upload

router = APIRouter()


@router.post("/format")
async def api_check_format(file: UploadFile = File(...)):
    """Kiểm tra định dạng bản cũ (giữ cho tích hợp cũ); giao diện dùng /docx."""
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


@router.get("/content_info")
async def api_content_info():
    """Phạm vi kiểm tra nội dung & căn cứ, mô hình AI đang dùng, tình trạng danh mục vbpl.vn."""
    return {"coverage": content_review.COVERAGE, "model": llm.resolve(llm.CHECK),
            "legal": {"enabled": config.LEGAL_REGISTRY_ENABLED, **legal_registry.status()}}


@router.post("/content_stream")
async def api_check_content_stream(file: UploadFile = File(...), ai: bool = Form(True), legal: bool = Form(True)):
    """SSE: report (dẫn chiếu, đối chiếu kho, logic bằng code) → legal (vbpl.vn) → related → ai."""
    path = await save_upload(file, {".docx", ".doc", ".pdf"}, config.UPLOAD_MAX_MB)
    try:
        events = runtime.open_event_stream(content_review.events, path, file.filename, ai, legal,
                                           slots=runtime.UPLOAD_SLOTS)
    except BaseException:
        remove_quietly(path)
        raise
    return StreamingResponse(runtime.sse_stream(events), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ------------------------------------------------------------------ thể thức & chính tả (.docx)

@router.get("/profiles")
async def api_check_profiles():
    """Các bộ thể thức có sẵn (để hiển thị và làm gốc cho bộ tùy chỉnh) và phạm vi kiểm tra."""
    profiles = docx_rules.load_profiles()
    return {"profiles": [{"key": key, "label": p.get("label"), "description": p.get("description"),
                          "page": p["page"], "font": p["font"], "body": p["body"], "closing": p["closing"],
                          "end_mark": p.get("end_mark"), "reference": p.get("reference")}
                         for key, p in profiles.items()],
            "coverage": docx_check.COVERAGE}


@router.post("/docx")
async def api_check_docx(file: UploadFile = File(...), profile: str = Form("skhcn"), custom: str | None = Form(None),
                         ai: bool = Form(True)):
    """SSE: report (thể thức + chính tả bằng code) rồi ai (chính tả bằng AI)."""
    custom_spec = None
    if profile == "custom":
        try:
            custom_spec = json.loads(custom or "{}")
            docx_rules.custom_profile(custom_spec)  # báo lỗi thông số trước khi tải tệp
        except (ValueError, docx_rules.ProfileError) as exc:
            raise HTTPException(400, f"Thông số thể thức tùy chỉnh không hợp lệ: {exc}")
    elif profile not in docx_rules.load_profiles():
        raise HTTPException(400, "Bộ thể thức không tồn tại.")
    path = await save_upload(file, {".docx"}, config.UPLOAD_MAX_MB)
    try:
        events = runtime.open_event_stream(docx_check.events, path, profile, custom_spec, ai, file.filename,
                                           slots=runtime.UPLOAD_SLOTS)
    except BaseException:
        remove_quietly(path)
        raise
    return StreamingResponse(runtime.sse_stream(events), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


class WordRequest(BaseModel):
    word: str = Field(..., min_length=1, max_length=60)


def _words() -> list[str]:
    return sorted({str(w).strip().lower() for w in app_settings.get(docx_check.CUSTOM_WORDS_KEY) or [] if str(w).strip()})


@router.get("/dictionary")
async def api_dictionary():
    """Từ điển riêng của đơn vị: tên riêng, thuật ngữ… không bị báo lỗi chính tả."""
    return {"words": _words()}


@router.post("/dictionary")
async def api_dictionary_add(req: WordRequest, request: Request, user: User = Depends(security.require_login)):
    word = unicodedata.normalize("NFC", " ".join(req.word.split())).lower()
    words = _words()
    if word not in words:
        if len(words) >= 5000:
            raise HTTPException(400, "Từ điển riêng đã đủ 5.000 từ.")
        app_settings.put(docx_check.CUSTOM_WORDS_KEY, sorted({*words, word}), user.id)
        security.audit(user, "dictionary_add", word, None, security.client_ip(request))
    return {"words": _words()}


@router.delete("/dictionary/{word}")
async def api_dictionary_remove(word: str, request: Request, user: User = Depends(security.require_login)):
    word = unicodedata.normalize("NFC", " ".join(word.split())).lower()
    app_settings.put(docx_check.CUSTOM_WORDS_KEY, [w for w in _words() if w != word], user.id)
    security.audit(user, "dictionary_remove", word, None, security.client_ip(request))
    return {"words": _words()}
