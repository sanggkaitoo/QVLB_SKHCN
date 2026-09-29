"""Chuẩn hóa số liệu trong văn bản hành chính Việt Nam (bằng code, không để LLM tính).

- Dấu phân cách: "1.234.567" (nghìn), "1.234,5" (thập phân kiểu Việt), "1,234.5" (kiểu Anh).
- Hệ số: nghìn/ngàn, triệu, tỷ/tỉ, nghìn tỷ; viết tắt "tr", "trđ".
- Loại đại lượng: tiền (quy về đồng), phần trăm, số đếm theo đơn vị (buổi, người, lượt...).
"""
from __future__ import annotations

import re
import unicodedata

_NUMBER_RE = re.compile(r"[-−]?\d[\d.,\s]*\d|[-−]?\d")
_MULTIPLIERS = (
    ("nghìn tỷ", 1e12), ("ngàn tỷ", 1e12), ("nghìn tỉ", 1e12),
    ("tỷ", 1e9), ("tỉ", 1e9),
    ("triệu", 1e6), ("trđ", 1e6), ("tr.đ", 1e6), ("tr", 1e6),
    ("nghìn", 1e3), ("ngàn", 1e3),
)
_MONEY_RE = re.compile(r"(đồng|vnđ|vnd|\bđ\b|trđ|tr\.đ)", re.IGNORECASE)
_PERCENT_RE = re.compile(r"(%|phần trăm)", re.IGNORECASE)


def _clean(text: str) -> str:
    return unicodedata.normalize("NFC", str(text or "")).strip().lower()


def parse_number(text: str) -> tuple[float | None, bool]:
    """Return (value, ambiguous) for the first number in text."""
    match = _NUMBER_RE.search(str(text or "").replace(" ", " "))
    if not match:
        return None, False
    raw = match.group(0)
    negative = raw.startswith(("-", "−"))
    digits = re.sub(r"\s+", "", raw.lstrip("-−"))
    # "1 234 567" is a thousands grouping only when every group after the first has three digits.
    if " " in raw.strip() and not re.fullmatch(r"\d{1,3}( \d{3})+([.,]\d+)?", raw.strip().lstrip("-−").strip()):
        digits = re.sub(r"\s+", "", raw.strip().split()[0].lstrip("-−"))
    ambiguous = False
    dots, commas = digits.count("."), digits.count(",")
    if dots and commas:
        decimal = "," if digits.rfind(",") > digits.rfind(".") else "."
        thousands = "." if decimal == "," else ","
        normalized = digits.replace(thousands, "").replace(decimal, ".")
    elif dots > 1:
        normalized = digits.replace(".", "")
    elif dots == 1:
        head, tail = digits.split(".")
        # Vietnamese thousands grouping ("1.234") vs a decimal ("2.5").
        normalized = head + tail if len(tail) == 3 else f"{head}.{tail}"
        ambiguous = len(tail) == 3 and len(head) <= 3
    elif commas > 1:
        normalized = digits.replace(",", "")
    elif commas == 1:
        head, tail = digits.split(",")
        # Vietnamese decimal comma ("1,5 tỷ"); "12,345" could also be an English grouping.
        normalized = f"{head}.{tail}"
        ambiguous = len(tail) == 3
    else:
        normalized = digits
    try:
        value = float(normalized)
    except ValueError:
        return None, True
    return (-value if negative else value), ambiguous


def _multiplier(text: str) -> float:
    for word, factor in _MULTIPLIERS:
        if re.search(rf"(?<![a-zà-ỹđ]){re.escape(word)}(?![a-zà-ỹđ])", text):
            return factor
    return 1.0


def normalize_quantity(value_text: str, unit_text: str | None = None) -> dict:
    """{'value', 'unit', 'kind', 'ambiguous'}; value is None when no number was found."""
    value_part, unit_part = _clean(value_text), _clean(unit_text)
    number, ambiguous = parse_number(value_part)
    combined = f"{value_part} {unit_part}".strip()
    if number is None:
        return {"value": None, "unit": unit_part or None, "kind": "unknown", "ambiguous": True}
    # Words after the number in value_text ("2,5 tỷ đồng") or in the unit field ("triệu đồng").
    after_number = _NUMBER_RE.sub(" ", combined, count=1)
    if _PERCENT_RE.search(combined):
        return {"value": number, "unit": "%", "kind": "percent", "ambiguous": ambiguous}
    factor = _multiplier(after_number)
    if _MONEY_RE.search(after_number) or (factor > 1 and re.search(r"(vốn|kinh phí|ngân sách|tiền)", combined)):
        return {"value": number * factor, "unit": "đồng", "kind": "money", "ambiguous": ambiguous}
    unit_source = unit_part or " ".join(after_number.split()[:2])
    unit = re.sub(r"^(nghìn tỷ|ngàn tỷ|tỷ|tỉ|triệu|nghìn|ngàn)(\s+|$)", "", unit_source).strip(" .,:;()") or None
    return {"value": number * factor, "unit": unit, "kind": "count", "ambiguous": ambiguous}


def format_number(value: float | None, unit: str | None = None) -> str:
    if value is None:
        return "không xác định"
    if abs(value - round(value)) < 1e-9:
        text = f"{int(round(value)):,}".replace(",", ".")
    else:
        text = f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{text} {unit}".strip() if unit else text


def digits_of(text: str) -> str:
    return re.sub(r"\D", "", str(text or ""))
