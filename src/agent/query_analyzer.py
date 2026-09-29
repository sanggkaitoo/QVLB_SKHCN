from __future__ import annotations

import calendar
import re

from src.agent.schemas import QueryIntent
from src.services.document_fields import extract_document_refs


_SCOPE_RE = re.compile(r"\b(các|tất cả|những|toàn bộ|mọi|từng|năm \d{4}|quý|tháng|trong năm|6 tháng)\b", re.IGNORECASE)
_AGG_VALUE_RE = re.compile(r"\b(tổng số|tổng cộng|cộng lại|tổng kinh phí|tổng giá trị|tổng hợp số liệu|trung bình|"
                           r"lớn nhất|nhỏ nhất|cao nhất|thấp nhất)\b", re.IGNORECASE)
_COUNT_DOCS_RE = re.compile(r"\b(bao nhiêu|số lượng|đếm( số)?)\s+(văn bản|công văn|quyết định|kế hoạch|báo cáo|"
                            r"tờ trình|thông báo|giấy mời|nghị quyết|chỉ thị|hướng dẫn)\b", re.IGNORECASE)
_COMPARE_WORDS = ("so sánh", "khác nhau", "đối chiếu", "giống và khác", "điểm khác", "điểm giống")
_LEGAL_WORDS = ("hiệu lực", "bãi bỏ", "thay thế", "sửa đổi", "còn áp dụng", "căn cứ vào", "căn cứ pháp lý",
                "được căn cứ")
_PARTIAL_REF_RE = re.compile(
    r"\b(?:văn bản|công văn|quyết định|kế hoạch|báo cáo|thông báo|tờ trình|giấy mời|nghị quyết|chỉ thị|"
    r"hướng dẫn|giấy phép)\s+(?:số\s+)?(\d{1,6})\b(?!\s*[/\-]\s*\w)",
    re.IGNORECASE,
)

_TYPE_LABELS = {
    "kế hoạch": "ke_hoach", "báo cáo": "bao_cao", "quyết định": "quyet_dinh", "công văn": "cong_van",
    "thông báo": "thong_bao", "nghị quyết": "nghi_quyet", "tờ trình": "to_trinh", "giấy mời": "giay_moi",
    "chỉ thị": "chi_thi", "hướng dẫn": "huong_dan", "chương trình": "chuong_trinh", "đề án": "de_an",
}
_ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "1": 1, "2": 2, "3": 3, "4": 4}


def detect_intent(query: str) -> QueryIntent:
    lowered = query.lower()
    refs = extract_document_refs(query)
    if _COUNT_DOCS_RE.search(lowered):
        return QueryIntent.AGGREGATE
    if _AGG_VALUE_RE.search(lowered) and (_SCOPE_RE.search(lowered) or not refs):
        return QueryIntent.AGGREGATE
    if any(word in lowered for word in _COMPARE_WORDS):
        return QueryIntent.COMPARE
    if any(word in lowered for word in _LEGAL_WORDS):
        return QueryIntent.LEGAL_STATUS
    if refs:
        return QueryIntent.EXACT_LOOKUP
    return QueryIntent.SEMANTIC_QA


def partial_reference_numbers(query: str) -> list[str]:
    """'công văn 2072' -> ['2072'] (chưa có phần ký hiệu sau dấu '/')."""
    return list(dict.fromkeys(match.group(1) for match in _PARTIAL_REF_RE.finditer(query or "")))


def _month_range(year: int, first_month: int, last_month: int) -> tuple[str, str]:
    last_day = calendar.monthrange(year, last_month)[1]
    return f"{year}-{first_month:02d}-01", f"{year}-{last_month:02d}-{last_day:02d}"


def detect_date_range(query: str) -> dict[str, str]:
    text = query.lower()
    explicit = re.search(r"từ(?: ngày)?\s+(\d{1,2})/(\d{1,2})/(\d{4})\s+(?:đến|tới)(?: ngày)?\s+(\d{1,2})/(\d{1,2})/(\d{4})", text)
    if explicit:
        d1, m1, y1, d2, m2, y2 = map(int, explicit.groups())
        try:
            calendar.monthrange(y1, m1), calendar.monthrange(y2, m2)
            return {"date_from": f"{y1}-{m1:02d}-{d1:02d}", "date_to": f"{y2}-{m2:02d}-{d2:02d}"}
        except calendar.IllegalMonthError:
            return {}
    month = re.search(r"tháng\s+(\d{1,2})\s*(?:/|năm)\s*(20\d{2})", text)
    if month and 1 <= int(month.group(1)) <= 12:
        start, end = _month_range(int(month.group(2)), int(month.group(1)), int(month.group(1)))
        return {"date_from": start, "date_to": end}
    quarter = re.search(r"quý\s+(iv|iii|ii|i|[1-4])\s*(?:/|năm)?\s*(20\d{2})", text)
    if quarter:
        number, year = _ROMAN[quarter.group(1)], int(quarter.group(2))
        start, end = _month_range(year, 3 * number - 2, 3 * number)
        return {"date_from": start, "date_to": end}
    half = re.search(r"6 tháng (đầu|cuối) năm\s+(20\d{2})", text)
    if half:
        year = int(half.group(2))
        start, end = _month_range(year, 1, 6) if half.group(1) == "đầu" else _month_range(year, 7, 12)
        return {"date_from": start, "date_to": end}
    year = re.search(r"\b(?:trong năm|năm)\s+(20\d{2})\b", text)
    if year:
        return {"date_from": f"{year.group(1)}-01-01", "date_to": f"{year.group(1)}-12-31"}
    return {}


def detect_filters(query: str) -> dict:
    lowered = query.lower()
    filters: dict = {}
    direction = re.search(r"\b(?:văn bản|công văn|quyết định|kế hoạch|báo cáo|tờ trình|thông báo|giấy mời)\s+(đi|đến)\b"
                          r"(?!\s+(?:các|những|ubnd|sở|bộ|cơ quan|đơn vị|ủy ban))", lowered)
    if direction:
        filters["huong"] = ["den" if direction.group(1) == "đến" else "di"]
    types = []
    for label, value in _TYPE_LABELS.items():
        pattern = rf"\b(tìm|các|những|danh sách|loại|văn bản|bao nhiêu|số lượng|đếm|trong)\s+(?:văn bản\s+)?{re.escape(label)}\b"
        if re.search(pattern, lowered):
            types.append(value)
    if types:
        filters["loai_vb"] = types
    agency = re.search(
        r"\b(?:của|do)\s+((?:ủy ban nhân dân|ubnd|sở|bộ|cục|trung tâm|ban)\b[^,?.]{2,80})",
        lowered,
    )
    if agency:
        filters["co_quan_ban_hanh"] = re.split(r"\s+(?:năm|trong|về|ban hành|ngày|từ|quý|tháng)\b",
                                               agency.group(1), maxsplit=1)[0].strip()
    filters.update(detect_date_range(query))
    return filters
