"""Thẩm định nội dung dự thảo bằng AI (mô hình gán cho "Kiểm tra dự thảo – thẩm định nội dung").

1. Đối chiếu có căn cứ: với mỗi văn bản được dẫn có trong kho, gửi các đoạn dự thảo nói về văn bản đó cùng
   trích đoạn NGUYÊN VĂN của văn bản đó; AI kết luận dự thảo ghi đúng/sai (thời hạn, số liệu, đơn vị, yêu cầu).
   Code kiểm lại: cụm dự thảo phải có trong đoạn, câu bằng chứng phải có nguyên văn trong văn bản được dẫn.
2. Rà soát nội dung: logic, số liệu, thẩm quyền, thời hạn, diễn đạt gây hiểu sai — KHÔNG báo chính tả/thể thức
   (tab "Thể thức & chính tả" đã làm). Chia theo đề mục, không cắt cụt văn bản dài.
"""
from __future__ import annotations

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor

from src.check.docx_model import fold
from src.core import llm, store

logger = logging.getLogger(__name__)

_CHUNK_CHARS = 4500
_SOURCE_CHARS = 7000
_MAX_GROUNDED = 6
_MAX_TABLE_CHARS = 4000
_REVIEW_ROLES = {"body", "list", "heading", "phu_luc_ref", "kinh_gui", "trich_yeu", "vv", "body_table"}
_NO_FIX = re.compile(r"không cần (sửa|chỉnh|thay)|đã đúng|không (có )?(lỗi|vấn đề)|giữ nguyên", re.I)
_FORMAT_TALK = re.compile(r"chính tả|lỗi gõ|gõ sai|viết hoa|dấu câu|dấu chấm|dấu phẩy|khoảng trắng|thể thức|"
                          r"phông chữ|cỡ chữ|căn lề|in đậm|in nghiêng|viết tắt", re.I)
CATEGORY_LABELS = {"logic": "Logic", "so_lieu": "Số liệu", "tham_quyen": "Thẩm quyền", "thoi_han": "Thời hạn",
                   "dien_dat": "Diễn đạt", "can_cu": "Đối chiếu văn bản dẫn"}
_STOP = {"va", "cua", "cac", "trong", "theo", "tren", "cho", "duoc", "voi", "nhung", "nay", "tai", "ve", "viec",
         "de", "thuc", "hien", "so", "ngay", "thang", "nam", "tinh", "lao", "cai", "khoa", "hoc", "cong", "nghe"}

CONTEXT = """Bối cảnh hành chính hiện hành (sau sắp xếp bộ máy năm 2025) — KHÔNG dùng hiểu biết cũ để bắt lỗi:
- Chính quyền địa phương 2 cấp (tỉnh, xã/phường), không còn cấp huyện. Tỉnh Lào Cai mới hợp nhất Lào Cai và Yên Bái
  (99 xã, phường); địa danh thuộc Yên Bái cũ (Văn Phú, Mù Cang Chải, phường Yên Bái…) nay thuộc tỉnh Lào Cai.
- Bộ/Sở Nông nghiệp và Môi trường (hợp nhất Nông nghiệp và PTNT với Tài nguyên và Môi trường; nhận thêm giảm nghèo);
  Bộ/Sở Khoa học và Công nghệ (hợp nhất Thông tin và Truyền thông); Bộ/Sở Nội vụ (nhận lao động, người có công);
  Bộ/Sở Tài chính (hợp nhất Kế hoạch và Đầu tư); Bộ/Sở Xây dựng (hợp nhất Giao thông vận tải).
- Không báo lỗi về tên cơ quan, chức năng nhiệm vụ của cơ quan, số lượng/tên đơn vị hành chính, địa danh, tên doanh
  nghiệp — những thông tin này có thể đã thay đổi so với hiểu biết của bạn.
- Không báo về đánh số đề mục, Phụ lục, Điều (đã kiểm tra bằng code)."""

SYSTEM_GROUNDED = """Bạn là chuyên viên pháp chế thẩm định dự thảo văn bản hành chính tiếng Việt.
Bạn nhận: (1) các đoạn dự thảo có nhắc tới văn bản X; (2) trích đoạn NGUYÊN VĂN của văn bản X lấy từ kho.
Nhiệm vụ: kiểm tra những điều dự thảo NÓI VỀ văn bản X (thời hạn, số liệu, đơn vị được giao, nội dung yêu cầu,
trích yếu) có đúng với văn bản X không.
Quy tắc:
- Chỉ đánh giá nội dung dự thảo gán cho văn bản X. Không đánh giá văn phong, chính tả, thể thức.
- "claim": chép NGUYÊN VĂN cụm trong đoạn dự thảo (tối đa 40 từ).
- "evidence": chép NGUYÊN VĂN một câu/cụm trong trích đoạn văn bản X làm bằng chứng (tối đa 60 từ).
- verdict: "sai" = mâu thuẫn rõ với văn bản X; "thieu" = dự thảo bỏ sót yêu cầu bắt buộc của X liên quan trực tiếp
  đến việc dự thảo đang làm (VD thời hạn, đơn vị đầu mối); "dung" = khớp; "khong_ro" = trích đoạn không đủ để kết luận.
- Không có bằng chứng nguyên văn thì KHÔNG được kết luận "sai" hay "thieu".
{context}
Trả về JSON: {"findings": [{"paragraph_id": "p12", "claim": "...", "verdict": "sai|thieu|dung|khong_ro",
"evidence": "...", "explanation": "ngắn gọn", "suggestion": "cách sửa (nếu có)", "confidence": 0.0-1.0}]}"""

SYSTEM_REVIEW = """Bạn là chuyên viên thẩm định nội dung dự thảo văn bản hành chính của cơ quan nhà nước Việt Nam.
{context}
Chỉ tìm các vấn đề NỘI DUNG thực sự, quan trọng (tối đa 6 vấn đề mỗi phần), thuộc các loại:
- "logic": mâu thuẫn giữa các đoạn; giao việc thiếu đơn vị chủ trì hoặc thời hạn; yêu cầu tự mâu thuẫn, không khả thi;
- "so_lieu": tổng không khớp các thành phần, tỷ lệ % tính sai, cùng một số liệu ghi khác nhau ở các chỗ;
- "tham_quyen": cơ quan ban hành tự giao việc/chỉ đạo/quyết định vượt thẩm quyền (VD Sở "giao" nhiệm vụ cho UBND tỉnh,
  Sở "chỉ đạo" Bộ). Lưu ý: "đề nghị" cơ quan ngang cấp phối hợp là BÌNH THƯỜNG; các gạch đầu dòng dưới mục
  "Kiến nghị/Đề xuất (với UBND tỉnh…)" có chủ thể ngầm là cấp trên được kiến nghị — không phải lỗi thẩm quyền;
- "thoi_han": thời hạn mâu thuẫn nhau hoặc trước ngày ban hành;
- "dien_dat": câu tối nghĩa có thể hiểu sai nội dung, chủ thể thực hiện.
KHÔNG báo: chính tả, lỗi gõ, dấu câu, viết hoa, thể thức trình bày, văn phong thuần tuý, gợi ý bổ sung chung chung,
yêu cầu "nêu rõ hơn" khi nội dung vẫn hiểu được.
"quote": chép NGUYÊN VĂN cụm trong đoạn (ngắn nhất đủ để định vị, tối đa 40 từ).
Chỉ báo khi chỉ ra được căn cứ ngay trong văn bản (đoạn mâu thuẫn, phép tính cụ thể); không có vấn đề thì trả
danh sách rỗng.
Trả về JSON: {"issues": [{"paragraph_id": "p12", "quote": "...", "category": "logic|so_lieu|tham_quyen|thoi_han|dien_dat",
"problem": "vấn đề ngắn gọn", "suggestion": "cách sửa", "confidence": 0.0-1.0}]}"""


def _norm(text: str) -> str:
    text = re.sub(r"[“”\"„]", '"', str(text or ""))
    text = re.sub(r"[‘’']", "'", text)
    return " ".join(text.split()).lower()


def _contains(haystack: str, needle: str, min_part: int = 12) -> bool:
    """needle (có thể có "…" ở giữa) xuất hiện nguyên văn trong haystack."""
    hay, parts = _norm(haystack), [p.strip(" .,;") for p in re.split(r"\.\.\.|…", _norm(needle))]
    parts = [p for p in parts if p]
    if not parts:
        return False
    return all(part in hay for part in parts) and sum(len(p) for p in parts) >= min(min_part, len(_norm(needle)))


def _keywords(text: str) -> set[str]:
    return {word for word in re.findall(r"[a-z0-9]+", fold(text)) if len(word) > 1 and word not in _STOP}


def source_passages(full_text: str, claims: list[str], limit: int = _SOURCE_CHARS) -> str:
    """Trích các đoạn của văn bản được dẫn liên quan nhất tới các đoạn dự thảo (giữ thứ tự gốc)."""
    blocks, current = [], ""
    for line in (full_text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        if current and len(current) + len(line) > 700:
            blocks.append(current)
            current = ""
        current = f"{current}\n{line}" if current else line
    if current:
        blocks.append(current)
    if sum(len(block) for block in blocks) <= limit:
        return "\n".join(blocks)
    wanted = set().union(*(_keywords(claim) for claim in claims)) if claims else set()
    numbers = set(re.findall(r"\d+(?:[/.]\d+)*", " ".join(claims)))
    scored = []
    for position, block in enumerate(blocks):
        words = _keywords(block)
        score = len(words & wanted) / (1 + len(words) ** 0.5)
        score += 0.6 * len(numbers & set(re.findall(r"\d+(?:[/.]\d+)*", block)))
        scored.append((score + (2 if position == 0 else 0), position))
    chosen, size = set(), 0
    for score, position in sorted(scored, reverse=True):
        if size + len(blocks[position]) > limit:
            continue
        chosen.add(position)
        size += len(blocks[position])
    return "\n…\n".join(blocks[position] for position in sorted(chosen))


def _paragraph_line(p) -> str:
    return f"p{p.index}: {' '.join(p.text.split())}"


# ------------------------------------------------------------------ 1. đối chiếu với văn bản được dẫn

def grounded_targets(refs, paragraphs_by_index: dict) -> list[dict]:
    """Các văn bản được dẫn có trong kho và được dự thảo nói tới ngoài dòng căn cứ."""
    targets = []
    for ref in refs:
        if not ref.kho:
            continue
        mentions = [paragraphs_by_index[number] for number in ref.mentions if number in paragraphs_by_index]
        mentions = [p for p in mentions if not fold(p.text).startswith("can cu") and len(p.text) > 40]
        if mentions:
            targets.append({"ref": ref, "paragraphs": mentions[:6]})
    targets.sort(key=lambda target: -len(target["paragraphs"]))
    return targets[:_MAX_GROUNDED]


def _ask_grounded(target: dict, meta: dict) -> dict:
    ref = target["ref"]
    document = store.get_document(ref.kho["id"]) or {}
    full_text = document.get("full_text") or ""
    if len(full_text) < 80:
        return {"ref": ref, "findings": [], "skipped": "Văn bản trong kho không có nội dung để đối chiếu."}
    claims = [p.text for p in target["paragraphs"]]
    passages = source_passages(full_text, claims)
    user = (f"Dự thảo: {meta.get('doc_type') or 'văn bản'} của {meta.get('issuer') or 'cơ quan soạn thảo'}"
            f" (ngày {meta.get('date_text') or 'chưa ghi'}).\n\n"
            f"ĐOẠN DỰ THẢO NHẮC TỚI {ref.label}:\n" + "\n".join(_paragraph_line(p) for p in target["paragraphs"]) +
            f"\n\nVĂN BẢN X = {ref.kho['so_ky_hieu']} ngày {ref.kho['ngay_ban_hanh']}"
            f" — {ref.kho.get('co_quan') or ''}: {ref.kho.get('trich_yeu') or ''}\nTRÍCH ĐOẠN NGUYÊN VĂN:\n{passages}")
    try:
        result = llm.extract_json(SYSTEM_GROUNDED.replace("{context}", CONTEXT), user, model=llm.CHECK, timeout=150)
    except RuntimeError as exc:
        if "exceeded" not in str(exc) or len(passages) < 2500:
            raise
        # Mô hình suy luận dùng hết giới hạn đầu ra: gửi lại với trích đoạn ngắn hơn.
        user = user.replace(passages, source_passages(full_text, claims, limit=_SOURCE_CHARS // 2))
        result = llm.extract_json(SYSTEM_GROUNDED.replace("{context}", CONTEXT), user, model=llm.CHECK, timeout=150)
    findings = result.get("findings") if isinstance(result, dict) else result
    return {"ref": ref, "findings": findings if isinstance(findings, list) else [], "source": full_text}


def filter_grounded(raw: dict, paragraphs_by_index: dict) -> tuple[list[dict], list[dict], int]:
    """(vấn đề, mục khớp, số mục bị loại)."""
    ref, source = raw["ref"], raw.get("source") or ""
    issues, verified, dropped, seen = [], [], 0, set()
    for item in raw.get("findings") or []:
        if not isinstance(item, dict):
            dropped += 1
            continue
        match = re.match(r"p?(\d+)$", str(item.get("paragraph_id", "")).strip())
        paragraph = paragraphs_by_index.get(int(match.group(1))) if match else None
        claim = " ".join(str(item.get("claim") or "").split())
        evidence = " ".join(str(item.get("evidence") or "").split())
        verdict = str(item.get("verdict") or "").strip().lower()
        try:
            confidence = float(item.get("confidence", 0.7))
        except (TypeError, ValueError):
            confidence = 0.7
        if paragraph is None or not claim or not _contains(paragraph.text, claim) or verdict not in ("sai", "thieu", "dung"):
            dropped += 1
            continue
        evidence_ok = bool(evidence) and _contains(source, evidence, min_part=15)
        key = (paragraph.index, _norm(claim))
        if key in seen:
            continue
        seen.add(key)
        if verdict == "dung":
            if evidence_ok:
                verified.append({"index": paragraph.index, "claim": claim, "evidence": evidence})
            continue
        if not evidence_ok or confidence < 0.5:
            dropped += 1
            continue
        issues.append({"category": "can_cu", "category_label": CATEGORY_LABELS["can_cu"],
                       "severity": "fail" if verdict == "sai" and confidence >= 0.8 else "warn",
                       "verdict": verdict, "index": paragraph.index, "excerpt": paragraph.excerpt(110),
                       "quote": claim, "evidence": evidence, "problem": str(item.get("explanation") or "").strip(),
                       "suggestion": str(item.get("suggestion") or "").strip() or None,
                       "source": {"label": ref.label, "so_ky_hieu": ref.kho["so_ky_hieu"], "url": ref.kho.get("url")},
                       "confidence": round(confidence, 2)})
    return issues, verified, dropped


# ------------------------------------------------------------------ 2. rà soát nội dung

_HEAD_ROLES = {"quoc_hieu", "tieu_ngu", "co_quan", "so_ky_hieu", "dia_danh", "ten_loai", "noi_nhan_title",
               "noi_nhan_item", "chuc_vu", "ky_quyen", "ky_ten", "ky_ghi_chu", "closing_other", "header_other"}


def review_scope(document) -> list:
    paragraphs = [p for p in document.paragraphs if not p.empty and p.role in _REVIEW_ROLES]
    if not paragraphs:  # không nhận diện được cấu trúc (giấy phép, biểu mẫu…): gửi mọi đoạn ngoài phần đầu/chữ ký
        paragraphs = [p for p in document.paragraphs
                      if not p.empty and p.role not in _HEAD_ROLES and len(p.text.strip()) > 20]
    table_chars = sum(len(p.text) for p in paragraphs if p.in_table)
    if table_chars > _MAX_TABLE_CHARS:  # bảng lớn (biểu số liệu): chỉ gửi phần lời
        paragraphs = [p for p in paragraphs if not p.in_table]
    return paragraphs


def chunks(paragraphs: list) -> list[list]:
    """Chia theo đề mục: ưu tiên cắt tại tiêu đề, không cắt giữa đoạn."""
    output, current, size = [], [], 0
    for p in paragraphs:
        heading = p.role == "heading"
        if current and (size + len(p.text) > _CHUNK_CHARS or (heading and size > _CHUNK_CHARS * 0.6)):
            output.append(current)
            current, size = [], 0
        current.append(p)
        size += len(p.text)
    if current:
        output.append(current)
    return output


def _ask_review(chunk: list, meta: dict, outline: str, position: int, total: int) -> list:
    lines = [f"{_paragraph_line(p)}{' [bảng]' if p.in_table else ''}" for p in chunk]
    user = (f"Thông tin dự thảo: {meta.get('doc_type') or 'văn bản'} số {meta.get('number') or '(chưa có số)'}"
            f" của {meta.get('issuer') or 'cơ quan soạn thảo'}, ngày {meta.get('date_text') or 'chưa ghi'}."
            f"\nTrích yếu: {meta.get('subject') or '(không rõ)'}"
            f"\nDàn ý toàn văn bản:\n{outline or '(không có đề mục)'}"
            f"\n\nPhần {position}/{total} của nội dung cần rà soát:\n" + "\n".join(lines))
    try:
        result = llm.extract_json(SYSTEM_REVIEW.replace("{context}", CONTEXT), user, model=llm.CHECK, timeout=180)
    except RuntimeError as exc:
        if "exceeded" not in str(exc) or len(chunk) < 2:
            raise
        # Mô hình suy luận dùng hết giới hạn đầu ra: chia đôi phần này rồi hỏi lại.
        half = len(chunk) // 2
        return (_ask_review(chunk[:half], meta, outline, position, total)
                + _ask_review(chunk[half:], meta, outline, position, total))
    issues = result.get("issues") if isinstance(result, dict) else result
    return issues if isinstance(issues, list) else []


def filter_review(raw: list, paragraphs_by_index: dict) -> tuple[list[dict], int]:
    kept, dropped, seen = [], 0, set()
    for item in raw:
        if not isinstance(item, dict):
            dropped += 1
            continue
        match = re.match(r"p?(\d+)$", str(item.get("paragraph_id", "")).strip())
        paragraph = paragraphs_by_index.get(int(match.group(1))) if match else None
        quote = " ".join(str(item.get("quote") or "").split())
        category = str(item.get("category") or "").strip().lower()
        problem = str(item.get("problem") or "").strip()
        suggestion = str(item.get("suggestion") or "").strip()
        try:
            confidence = float(item.get("confidence", 0.7))
        except (TypeError, ValueError):
            confidence = 0.7
        reasons = [
            paragraph is None,
            not quote or not problem,
            paragraph is not None and not _contains(paragraph.text, quote, min_part=6),
            category not in CATEGORY_LABELS or category == "can_cu",
            bool(_FORMAT_TALK.search(problem)),
            bool(_NO_FIX.search(problem)),
            confidence < 0.5,
        ]
        key = (paragraph.index if paragraph else None, _norm(quote))
        if any(reasons) or key in seen:
            dropped += 1
            continue
        seen.add(key)
        # Rà soát tự do không có bằng chứng đối chiếu: luôn là "cần xem xét", chuyên viên quyết định.
        kept.append({"category": category, "category_label": CATEGORY_LABELS[category],
                     "severity": "warn", "index": paragraph.index,
                     "excerpt": paragraph.excerpt(110), "quote": quote, "problem": problem,
                     "suggestion": suggestion or None, "confidence": round(confidence, 2)})
    return kept, dropped


def check(document, refs, meta: dict) -> dict:
    model = llm.resolve(llm.CHECK)
    started = time.perf_counter()
    by_index = {p.index: p for p in document.paragraphs}
    targets = grounded_targets(refs, by_index)
    scope = review_scope(document)
    if not scope and not targets:
        return {"status": "skipped", "model": model, "issues": [], "verified": [],
                "message": "Không xác định được phần nội dung để gửi AI."}
    outline = "\n".join(f"- p{p.index}: {p.excerpt(90)}" for p in scope if p.role == "heading")[:2500]
    parts = chunks(scope)
    errors, issues, verified, dropped = [], [], [], 0
    with ThreadPoolExecutor(max_workers=4) as pool:
        grounded = [pool.submit(_ask_grounded, target, meta) for target in targets]
        reviews = [pool.submit(_ask_review, chunk, meta, outline, position, len(parts))
                   for position, chunk in enumerate(parts, 1)]
        for future in grounded:
            try:
                found, ok, lost = filter_grounded(future.result(), by_index)
                issues.extend(found)
                verified.extend(ok)
                dropped += lost
            except Exception as exc:
                logger.warning("Đối chiếu văn bản dẫn bằng AI lỗi: %s", exc)
                errors.append(f"{type(exc).__name__}: {str(exc)[:160]}")
        raw_review = []
        for future in reviews:
            try:
                raw_review.extend(future.result())
            except Exception as exc:
                logger.warning("Rà soát nội dung bằng AI lỗi: %s", exc)
                errors.append(f"{type(exc).__name__}: {str(exc)[:160]}")
    found, lost = filter_review(raw_review, by_index)
    issues.extend(found)
    dropped += lost
    issues.sort(key=lambda issue: (issue["index"], issue["category"]))
    calls = len(targets) + len(parts)
    status = "error" if errors and len(errors) == calls else "done"
    return {"status": status, "model": model, "issues": issues, "verified": verified, "dropped": dropped,
            "grounded": [{"label": t["ref"].label, "so_ky_hieu": t["ref"].kho["so_ky_hieu"],
                          "paragraphs": len(t["paragraphs"])} for t in targets],
            "chunks": len(parts), "paragraphs": len(scope), "errors": errors[:3],
            "seconds": round(time.perf_counter() - started, 1),
            "message": ("Không gọi được mô hình AI: " + errors[0]) if status == "error" else None}
