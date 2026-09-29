"""Trích quan hệ giữa văn bản (căn cứ, sửa đổi, thay thế, bãi bỏ, liên quan) bằng quy tắc.

Kết quả chỉ là gợi ý (verified = false); cán bộ xác minh trước khi coi là kết luận pháp lý.
"""
from __future__ import annotations

import re

from src.services.document_fields import DOCUMENT_REF_RE, extract_document_refs, normalize_document_ref

_SENTENCE_RE = re.compile(r"(?<=[.;])\s+|\n+")
_TYPED_PATTERNS = (
    ("bai_bo", re.compile(r"\bbãi bỏ\b", re.IGNORECASE), 0.65),
    ("thay_the", re.compile(r"\bthay thế\b", re.IGNORECASE), 0.65),
    ("sua_doi", re.compile(r"\bsửa đổi\b", re.IGNORECASE), 0.6),
)
_RELATED_RE = re.compile(r"^\s*(thực hiện|triển khai|theo|căn cứ vào|trên cơ sở|phúc đáp|tiếp theo)\b", re.IGNORECASE)
_MAX_RELATIONS = 40


def _refs_after(sentence: str, keyword: re.Pattern) -> list[str]:
    match = keyword.search(sentence)
    if not match:
        return []
    return extract_document_refs(sentence[match.end():])


def extract_relations(text: str, own_ref: str | None = None) -> list[dict]:
    own = normalize_document_ref(own_ref) if own_ref else None
    found: dict[tuple[str, str], dict] = {}

    def add(relation_type: str, ref: str, evidence: str, confidence: float):
        if not ref or ref == own or len(found) >= _MAX_RELATIONS:
            return
        key = (relation_type, ref)
        if key not in found:
            found[key] = {
                "relation_type": relation_type,
                "target_ref_text": ref,
                "normalized_target_ref": ref,
                "evidence_text": " ".join(evidence.split())[:400],
                "confidence": confidence,
            }

    for line in (text or "").splitlines():
        stripped = line.strip()
        if not stripped or not DOCUMENT_REF_RE.search(stripped):
            continue
        if re.match(r"^căn cứ\b", stripped, re.IGNORECASE):
            for ref in extract_document_refs(stripped):
                add("can_cu", ref, stripped, 0.8)
            continue
        for sentence in _SENTENCE_RE.split(stripped):
            typed = False
            for relation_type, keyword, confidence in _TYPED_PATTERNS:
                refs = _refs_after(sentence, keyword)
                for ref in refs:
                    add(relation_type, ref, sentence, confidence)
                typed = typed or bool(refs)
            if not typed and _RELATED_RE.match(sentence):
                for ref in extract_document_refs(sentence):
                    add("lien_quan", ref, sentence, 0.5)
    return list(found.values())
