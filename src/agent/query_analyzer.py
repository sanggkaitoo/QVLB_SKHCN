from __future__ import annotations

import re

from src.agent.schemas import QueryIntent
from src.services.retrieval_srv import extract_document_refs


_AGGREGATE_WORDS = ("tổng số", "bao nhiêu văn bản", "đếm", "tổng cộng", "cộng lại")
_COMPARE_WORDS = ("so sánh", "khác nhau", "đối chiếu", "giống và khác")
_LEGAL_WORDS = ("hiệu lực", "bãi bỏ", "thay thế", "sửa đổi", "còn áp dụng")


def detect_intent(query: str) -> QueryIntent:
    lowered = query.lower()
    if extract_document_refs(query):
        if any(word in lowered for word in _LEGAL_WORDS):
            return QueryIntent.LEGAL_STATUS
        return QueryIntent.EXACT_LOOKUP
    if any(word in lowered for word in _AGGREGATE_WORDS):
        return QueryIntent.AGGREGATE
    if any(word in lowered for word in _COMPARE_WORDS):
        return QueryIntent.COMPARE
    if any(word in lowered for word in _LEGAL_WORDS):
        return QueryIntent.LEGAL_STATUS
    return QueryIntent.SEMANTIC_QA


def detect_filters(query: str) -> dict[str, str]:
    lowered = query.lower()
    filters: dict[str, str] = {}
    if "văn bản đến" in lowered:
        filters["huong"] = "den"
    elif "văn bản đi" in lowered:
        filters["huong"] = "di"
    type_map = {
        "kế hoạch": "ke_hoach", "báo cáo": "bao_cao", "quyết định": "quyet_dinh",
        "công văn": "cong_van", "thông báo": "thong_bao", "nghị quyết": "nghi_quyet",
    }
    for label, value in type_map.items():
        pattern = rf"\b(tìm|các|những|danh sách|loại|văn bản)\s+(?:văn bản\s+)?{re.escape(label)}\b"
        if re.search(pattern, lowered):
            filters["loai_vb"] = value
            break
    agency = re.search(
        r"\b(?:của|do)\s+((?:ủy ban nhân dân|ubnd|sở|bộ|cục|trung tâm)\b[^,?.]{2,80})",
        lowered,
    )
    if agency:
        filters["co_quan_ban_hanh"] = agency.group(1).strip()
    year = re.search(r"\b(20\d{2})\b", query)
    if year:
        filters["date_from"] = f"{year.group(1)}-01-01"
        filters["date_to"] = f"{year.group(1)}-12-31"
    return filters
