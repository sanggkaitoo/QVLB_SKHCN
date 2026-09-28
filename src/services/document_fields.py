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
