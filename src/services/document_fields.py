from datetime import date
import re
import unicodedata


def normalize_agency(value):
    value = str(value or "").lower().replace("đ", "d")
    value = "".join(c for c in unicodedata.normalize("NFD", value) if unicodedata.category(c) != "Mn")
    value = re.sub(r"\bubnd\b", "uy ban nhan dan", value)
    return " ".join(value.split())


def issued_day(value):
    try:
        return date.fromisoformat(str(value)[:10]).toordinal()
    except (ValueError, TypeError):
        return None


def normalize_document_ref(value: str | None) -> str:
    value = (value or "").replace("đ", "d").replace("Đ", "D")
    value = value.replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", "", value).upper()


FILE_ROLE_LABELS = {"chinh": "Văn bản chính", "ban_sao": "Bản sao cùng nội dung", "dinh_kem": "Tệp đính kèm"}

_LIST_FILTERS = ("loai_vb", "huong", "linh_vuc")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TOKEN_RE = re.compile(r"^[a-zđ_]{1,40}$")


def _as_list(value) -> list[str]:
    if value is None:
        return []
    items = value if isinstance(value, (list, tuple, set)) else str(value).split(",")
    output = []
    for item in items:
        text = str(item or "").strip().lower()
        if text and _TOKEN_RE.match(text) and text not in output:
            output.append(text)
    return output


def normalize_filters(raw: dict | None) -> dict:
    """Chuẩn hóa bộ lọc từ UI/planner: danh sách mã, ngày ISO, cơ quan dạng chuỗi.

    Giá trị không hợp lệ bị bỏ qua thay vì làm hỏng truy vấn.
    """
    raw = raw or {}
    filters: dict = {}
    for key in _LIST_FILTERS:
        values = _as_list(raw.get(key))
        if key == "huong":
            values = [value for value in values if value in {"di", "den"}]
        if values:
            filters[key] = values
    for key in ("date_from", "date_to"):
        value = str(raw.get(key) or "").strip()[:10]
        if _DATE_RE.match(value) and issued_day(value) is not None:
            filters[key] = value
    agency = " ".join(str(raw.get("co_quan_ban_hanh") or "").split())[:120]
    if agency:
        filters["co_quan_ban_hanh"] = agency
    return filters


# Số ký hiệu: "215/KH-UBND", "12/2024/NĐ-CP", "57-NQ/TW". Phần sau dấu "/" phải có chữ cái
# để không nhầm ngày tháng ("12/2024") hay phân số ("3/4") thành số ký hiệu.
DOCUMENT_REF_RE = re.compile(
    r"(?<![\w/])("
    r"\d{1,6}\s*/\s*[0-9A-ZÀ-ỸĐ][0-9A-ZÀ-ỸĐ./\-]*(?:\s*-\s*[0-9A-ZÀ-ỸĐ./\-]+)*"
    r"|\d{1,6}-[A-ZÀ-ỸĐ]{1,10}/[0-9A-ZÀ-ỸĐ][0-9A-ZÀ-ỸĐ\-]*"
    r")(?!\w)",
    re.IGNORECASE,
)


def extract_document_refs(text: str) -> list[str]:
    refs: list[str] = []
    for match in DOCUMENT_REF_RE.finditer(text or ""):
        value = normalize_document_ref(match.group(1).rstrip(".,;-/"))
        suffix = value.split("/", 1)[1] if "/" in value else ""
        if value and re.search(r"[A-Z]", suffix) and value not in refs:
            refs.append(value)
    return refs


_PLACEHOLDER_RE = re.compile(r"(undefined|null|none|n/a)", re.IGNORECASE)
_HEADER_REF_RE = re.compile(r"Số\s*:?\s*(\d{0,6})\s*(/\s*[A-ZĐ][A-ZĐ0-9\-\s]{1,30}?)\s*(?:\n|$)", re.IGNORECASE)


def clean_placeholder(value) -> str:
    """Bỏ chuỗi giữ chỗ do giao diện QLVB sinh ra ('undefined', 'null')."""
    return " ".join(_PLACEHOLDER_RE.sub(" ", str(value or "")).split()).strip(" -/")


def reconcile_reference(raw_ref: str | None, extracted_ref: str | None, text: str = "") -> str | None:
    """Chọn số ký hiệu hợp lệ: giá trị QLVB đã làm sạch, hoặc dựng lại từ dòng 'Số: .../...' ở đầu văn bản
    (văn bản ký số thường tách con số khỏi phần ký hiệu), hoặc giá trị AI trích nếu cùng số."""
    cleaned = clean_placeholder(raw_ref)
    if cleaned and extract_document_refs(cleaned):
        return cleaned
    number = re.match(r"\d+", cleaned or "")
    number = number.group(0) if number else None
    header = _HEADER_REF_RE.search(text[:2500] or "")
    if header:
        head_number = header.group(1) or number
        if head_number and (not number or head_number == number):
            suffix = re.sub(r"\s+", "", header.group(2))
            candidate = f"{head_number}{suffix}"
            if extract_document_refs(candidate):
                return candidate
    extracted = clean_placeholder(extracted_ref)
    if extracted and extract_document_refs(extracted) and (not number or extracted.split("/")[0].split("-")[0] == number):
        return extracted
    return cleaned or extracted or None
