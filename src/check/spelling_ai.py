"""Kiểm tra chính tả bằng AI (mô hình gán cho tính năng "Kiểm tra dự thảo – chính tả").

Chỉ gửi phần nội dung đã nhận diện (không gửi tệp Word, tên tệp, bảng, nơi nhận, chữ ký, phụ lục).
Kết quả AI luôn được lọc lại bằng code vì mô hình có thể trả lỗi giả:
  - đoạn phải tồn tại và cụm "phát hiện" phải thực sự có trong đoạn;
  - bỏ mục có phát hiện = gợi ý, giải thích kiểu "không cần sửa", số/mã/chữ viết tắt;
  - "./." cuối nội dung là đúng — không bao giờ báo lỗi phần có "./.";
  - độ tin cậy < 0,5 bỏ; 0,5–0,8 "cần xem xét"; ≥ 0,8 "khả năng sai cao".
"""
from __future__ import annotations

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor

from src.core import llm

logger = logging.getLogger(__name__)

_CHUNK_CHARS = 4500
_MAX_PARAGRAPHS = 400
_AI_ROLES = {"body", "list", "heading", "phu_luc_ref", "trich_yeu", "vv", "kinh_gui"}
_NO_FIX = re.compile(r"không cần (sửa|chỉnh|thay)|đã đúng|không (có )?lỗi|giữ nguyên|không sai", re.I)
_CODE_LIKE = re.compile(r"^[\d\W_]+$|^[A-ZĐ0-9/().\-]{2,}$|\d+/\S+")

SYSTEM = """Bạn là chuyên viên rà soát chính tả văn bản hành chính tiếng Việt.
Nhiệm vụ DUY NHẤT: tìm lỗi chính tả, lỗi gõ phím, lặp từ, sai dấu thanh/dấu câu rõ ràng.
Quy tắc bắt buộc:
- KHÔNG sửa văn phong, KHÔNG viết lại câu, KHÔNG thay đổi nội dung hành chính.
- KHÔNG coi là lỗi: tên người, địa danh, tên cơ quan, chữ viết tắt (UBND, HĐND, SKHCN…), số, ngày tháng,
  số ký hiệu văn bản (vd 30/2020/NĐ-CP), mã hồ sơ, từ ngữ chuyên ngành và các từ trong danh sách cho phép.
- Cả hai kiểu đặt dấu thanh cũ/mới đều đúng (hoà/hòa, thuỷ/thủy, kỹ/kĩ, lý/lí).
- Ký hiệu "./." ở cuối đoạn nội dung cuối cùng là ĐÚNG và BẮT BUỘC; tuyệt đối không báo lỗi hay đề nghị bỏ nó.
- Chỉ báo lỗi khi chắc chắn; "original" phải là cụm chép NGUYÊN VĂN từ đoạn, ngắn nhất có thể (1–4 từ),
  "suggestion" phải KHÁC "original". Không có lỗi thì trả danh sách rỗng.
Trả về JSON: {"errors": [{"paragraph_id": "p12", "original": "...", "suggestion": "...",
"category": "chính tả|lặp từ|dấu câu|gõ phím", "explanation": "ngắn gọn", "confidence": 0.0-1.0}]}"""


def scope(document, structure) -> list:
    """Đoạn gửi AI: phần đầu cần thiết + nội dung (không bảng, không nơi nhận/chữ ký/phụ lục)."""
    paragraphs = []
    for p in document.paragraphs:
        if p.empty or p.in_table and p.role not in ("vv", "kinh_gui", "trich_yeu"):
            continue
        if p.role in _AI_ROLES:
            paragraphs.append(p)
    return paragraphs[:_MAX_PARAGRAPHS]


def _chunks(paragraphs: list) -> list[list]:
    chunks, current, size = [], [], 0
    for p in paragraphs:
        if current and size + len(p.text) > _CHUNK_CHARS:
            chunks.append(current)
            current, size = [], 0
        current.append(p)
        size += len(p.text)
    if current:
        chunks.append(current)
    return chunks


def _ask(chunk: list, last_index: int | None, allowed_words: list[str]) -> list[dict]:
    lines = []
    for p in chunk:
        note = " [ĐOẠN NỘI DUNG CUỐI — kết thúc bằng ./. là đúng]" if p.index == last_index else ""
        lines.append(f"p{p.index}{note}: {' '.join(p.text.split())}")
    user = ("Danh sách từ được phép (không báo lỗi): " + (", ".join(allowed_words[:200]) or "(trống)") +
            "\n\nCác đoạn cần kiểm tra:\n" + "\n".join(lines))
    result = llm.extract_json(SYSTEM, user, model=llm.SPELL, timeout=90, max_tokens=3000)
    if isinstance(result, dict):
        result = result.get("errors") or []
    return result if isinstance(result, list) else []


def _norm(text: str) -> str:
    return " ".join(str(text or "").split()).lower()


def filter_errors(raw: list[dict], paragraphs: dict[int, object], code_issues: list[dict],
                  last_index: int | None) -> tuple[list[dict], int]:
    """Lọc kết quả AI; trả (lỗi hợp lệ, số mục bị loại)."""
    kept, dropped, seen = [], 0, set()
    code_spans = {(issue["index"], _norm(issue.get("original"))) for issue in code_issues if issue.get("original")}
    for item in raw:
        if not isinstance(item, dict):
            dropped += 1
            continue
        match = re.match(r"p?(\d+)$", str(item.get("paragraph_id", "")).strip())
        paragraph = paragraphs.get(int(match.group(1))) if match else None
        original = " ".join(str(item.get("original") or "").split())
        suggestion = " ".join(str(item.get("suggestion") or "").split())
        explanation = str(item.get("explanation") or "").strip()
        try:
            confidence = float(item.get("confidence", 0.7))
        except (TypeError, ValueError):
            confidence = 0.7
        reasons = [
            paragraph is None,
            not original or not suggestion,
            paragraph is not None and original.lower() not in " ".join(paragraph.text.split()).lower(),
            _norm(original) == _norm(suggestion),
            bool(_NO_FIX.search(explanation)),
            bool(_CODE_LIKE.search(original)),
            "./." in original,
            "./." in suggestion and paragraph is not None and paragraph.index != last_index,
            confidence < 0.5,
        ]
        key = (paragraph.index if paragraph else None, _norm(original))
        if any(reasons) or key in seen or key in code_spans:
            dropped += 1
            continue
        seen.add(key)
        kept.append({"index": paragraph.index, "excerpt": paragraph.excerpt(), "category": "ai",
                     "severity": "fail" if confidence >= 0.8 else "warn", "original": original,
                     "suggestion": suggestion, "message": explanation or str(item.get("category") or "Chính tả"),
                     "confidence": round(confidence, 2), "source": "ai"})
    return kept, dropped


def check(document, structure, code_issues: list[dict], allowed_words: list[str]) -> dict:
    paragraphs = scope(document, structure)
    model = llm.resolve(llm.SPELL)
    if not paragraphs:
        return {"status": "skipped", "model": model, "message": "Không xác định được phần nội dung để gửi AI.",
                "issues": [], "paragraphs": 0}
    last_index = document.paragraphs[structure.last_body].index if structure.last_body is not None else None
    started = time.perf_counter()
    raw, errors = [], []
    chunks = _chunks(paragraphs)
    with ThreadPoolExecutor(max_workers=min(3, len(chunks))) as pool:
        futures = [pool.submit(_ask, chunk, last_index, allowed_words) for chunk in chunks]
        for future in futures:
            try:
                raw.extend(future.result())
            except Exception as exc:
                logger.warning("Kiểm tra chính tả AI lỗi: %s", exc)
                errors.append(f"{type(exc).__name__}: {str(exc)[:160]}")
    issues, dropped = filter_errors(raw, {p.index: p for p in paragraphs}, code_issues, last_index)
    status = "error" if errors and len(errors) == len(chunks) else "done"
    return {"status": status, "model": model, "paragraphs": len(paragraphs), "chunks": len(chunks),
            "issues": issues, "dropped": dropped, "errors": errors[:3],
            "seconds": round(time.perf_counter() - started, 1),
            "message": ("Không gọi được mô hình AI: " + errors[0]) if status == "error" else None}
