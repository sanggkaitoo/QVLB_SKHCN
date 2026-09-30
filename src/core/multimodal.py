"""Gọi model với đầu vào âm thanh (gỡ băng) hoặc ảnh (OCR), theo model admin gán cho từng tính năng.

Trước khi gọi luôn kiểm tra model có nhận loại dữ liệu đó không (model_catalog.supports), để
báo lỗi rõ ràng thay vì gửi âm thanh cho một model chỉ đọc được văn bản.
"""
from __future__ import annotations

import base64
import logging
import os

import requests

from src.core import anthropic_provider, config, llm
from src.services import model_catalog

logger = logging.getLogger(__name__)

# Định dạng âm thanh gửi kèm trong tin nhắn (input_audio) mà OpenRouter/OpenAI chấp nhận.
_INLINE_AUDIO_FORMATS = {".mp3": "mp3", ".wav": "wav", ".m4a": "m4a", ".aac": "aac", ".ogg": "ogg", ".flac": "flac"}
_OPENAI_CHAT_AUDIO = {".mp3": "mp3", ".wav": "wav"}
_OPENAI_TRANSCRIBE = {".mp3", ".mp4", ".m4a", ".wav", ".webm", ".ogg", ".flac"}
_OPENAI_TRANSCRIBE_MAX_MB = 25


class UnsupportedInput(ValueError):
    """Model được gán không xử lý được tệp/loại dữ liệu này (thông điệp hiển thị cho người dùng)."""


def _checked(task: str) -> tuple[str, str, str]:
    spec = llm.resolve("@" + task)
    ok, reason = model_catalog.supports(spec, task)
    if not ok:
        raise UnsupportedInput(reason + " Vui lòng nhờ quản trị viên chọn model khác cho tính năng này.")
    provider, model_id = llm.split_spec(spec)
    if not llm.provider_key(provider):
        raise UnsupportedInput(f"Chưa cấu hình khoá API {llm.PROVIDERS[provider]} cho tính năng này.")
    return spec, provider, model_id


# ------------------------------------------------------------------ gỡ băng

def _gemini_transcribe(model_id: str, path: str, prompt: str) -> str:
    import google.generativeai as genai
    genai.configure(api_key=llm.provider_key("gemini"))
    uploaded = genai.upload_file(path=path)
    try:
        model = genai.GenerativeModel(model_id)
        return model.generate_content([prompt, uploaded], request_options={"timeout": 600}).text
    finally:
        # Xóa tệp trên máy chủ Google cả khi gỡ băng lỗi.
        try:
            genai.delete_file(uploaded.name)
        except Exception as exc:
            logger.warning("Không xóa được tệp âm thanh trên Gemini: %s", exc)


def _chat_audio(provider: str, model_id: str, path: str, prompt: str, audio_format: str) -> str:
    with open(path, "rb") as stream:
        data = base64.b64encode(stream.read()).decode("ascii")
    client = llm._openai_client(provider).with_options(timeout=600, max_retries=0)
    response = client.chat.completions.create(model=model_id, messages=[{"role": "user", "content": [
        {"type": "text", "text": prompt},
        {"type": "input_audio", "input_audio": {"data": data, "format": audio_format}},
    ]}])
    if not response.choices:
        raise RuntimeError("Model không trả kết quả gỡ băng")
    return response.choices[0].message.content or ""


def transcribe(path: str, prompt: str) -> tuple[str, str]:
    """Trả (văn bản, model đã dùng)."""
    spec, provider, model_id = _checked("transcribe")
    extension = os.path.splitext(path)[1].lower()
    if provider == "gemini":
        return _gemini_transcribe(model_id, path, prompt), spec
    if provider == "openai" and ("transcribe" in model_id or model_id.startswith("whisper")):
        if extension not in _OPENAI_TRANSCRIBE:
            raise UnsupportedInput(f"Model {model_id} không nhận tệp {extension}. Hãy dùng mp3, m4a, wav, webm, ogg hoặc flac.")
        if os.path.getsize(path) > _OPENAI_TRANSCRIBE_MAX_MB * 1024 * 1024:
            raise UnsupportedInput(f"Model {model_id} chỉ nhận tệp tối đa {_OPENAI_TRANSCRIBE_MAX_MB} MB.")
        client = llm._openai_client("openai").with_options(timeout=600, max_retries=0)
        with open(path, "rb") as stream:
            result = client.audio.transcriptions.create(model=model_id, file=stream, language="vi", prompt=prompt[:200])
        return result.text, spec
    formats = _OPENAI_CHAT_AUDIO if provider == "openai" else _INLINE_AUDIO_FORMATS
    if extension not in formats:
        raise UnsupportedInput(f"Model đang dùng cho gỡ băng không nhận tệp {extension}. "
                               f"Định dạng được hỗ trợ: {', '.join(sorted(formats))}.")
    return _chat_audio(provider, model_id, path, prompt, formats[extension]), spec


# ------------------------------------------------------------------ OCR

def ocr_batch(prompt: str, images: list[tuple[str, str]]) -> str:
    """images: [(mime, base64)] — một lô trang; trả văn bản nhận dạng."""
    spec, provider, model_id = _checked("ocr")
    if provider == "anthropic":
        content = [{"type": "image", "source": {"type": "base64", "media_type": mime, "data": data}}
                   for mime, data in images] + [{"type": "text", "text": prompt}]
        return anthropic_provider.chat(llm.provider_key("anthropic"), model_id, "Bạn là hệ thống OCR tiếng Việt.",
                                       content, max_tokens=16000, timeout=config.OCR_REQUEST_TIMEOUT_SECONDS,
                                       effort="low")
    if provider == "gemini":
        import google.generativeai as genai
        genai.configure(api_key=llm.provider_key("gemini"))
        parts = [prompt, *({"mime_type": mime, "data": base64.b64decode(data)} for mime, data in images)]
        return genai.GenerativeModel(model_id).generate_content(
            parts, request_options={"timeout": config.OCR_REQUEST_TIMEOUT_SECONDS}).text
    content = [{"type": "text", "text": prompt},
               *({"type": "image_url", "image_url": {"url": f"data:{mime};base64,{data}"}} for mime, data in images)]
    if provider == "local":
        content[0]["text"] = "Multi page parsing."  # lệnh riêng của máy chủ Unlimited-OCR
        response = requests.post(f"{config.OCR_SERVER_URL.rstrip('/')}/v1/chat/completions", json={
            "model": model_id, "messages": [{"role": "user", "content": content}], "temperature": 0.0,
            "max_tokens": 16000}, timeout=config.OCR_REQUEST_TIMEOUT_SECONDS)
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]
    client = llm._openai_client(provider).with_options(timeout=config.OCR_REQUEST_TIMEOUT_SECONDS, max_retries=0)
    params = llm._openai_params(provider, model_id, 0.0, 16000, "low")
    response = client.chat.completions.create(model=model_id, messages=[{"role": "user", "content": content}], **params)
    if not response.choices:
        raise RuntimeError("Model không trả kết quả OCR")
    return response.choices[0].message.content or ""
