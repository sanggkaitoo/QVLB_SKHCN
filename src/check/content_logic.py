"""Kiểm tra logic nội dung dự thảo bằng code (không dùng AI).

- Thời gian: ngày không tồn tại (30/02), thứ trong tuần không khớp ngày, thời hạn đã qua so với ngày văn bản,
  khoảng "từ ngày … đến ngày …" ngược.
- Số liệu: số viết bằng số và bằng chữ không khớp ("05 (bốn) ngày", "1.200.000 đồng (Một triệu hai trăm…)").
- Dẫn chiếu nội bộ: "Phụ lục 02" không có trong tệp, "Điều 5" khi văn bản chỉ có 4 điều,
  đánh số Điều / đề mục La Mã bị nhảy hoặc trùng.
"""
from __future__ import annotations

import datetime as dt
import re

from src.check.docx_model import fold

_DATE_TEXT = (r"(?:ngày\s*)?(\d{1,2})\s*(?:tháng\s*|/\s*|-\s*|\.\s*)(\d{1,2})\s*(?:năm\s*|/\s*|-\s*|\.\s*)(\d{4})")
_DATE_RE = re.compile(r"(?<![\d/.-])" + _DATE_TEXT + r"(?![\d/]|-\d)", re.IGNORECASE)
_WEEKDAYS = {"hai": 0, "2": 0, "ba": 1, "3": 1, "tu": 2, "4": 2, "nam": 3, "5": 3, "sau": 4, "6": 4,
             "bay": 5, "7": 5, "chu nhat": 6, "cn": 6}
WEEKDAY_NAMES = ["Thứ Hai", "Thứ Ba", "Thứ Tư", "Thứ Năm", "Thứ Sáu", "Thứ Bảy", "Chủ nhật"]
_WEEKDAY = r"(thứ\s*(?:hai|ba|tư|năm|sáu|bảy|[2-7])|chủ\s*nhật)"
_WEEKDAY_BEFORE = re.compile(r"(?<!lần )(?<!lần thứ )\b" + _WEEKDAY + r"\s*[,(\-–]?\s*$", re.IGNORECASE)
_WEEKDAY_AFTER = re.compile(r"^\s*[,(\-–]?\s*" + _WEEKDAY + r"\b", re.IGNORECASE)
_DEADLINE = re.compile(r"(trước|chậm nhất(?:\s+là)?|không muộn hơn|hạn chót|hạn cuối|thời hạn(?:\s+gửi)?(?:\s+là)?|"
                       r"hoàn thành(?:\s+xong)?(?:\s+trong)?|xong trước|kết thúc(?:\s+vào)?)\s*(?:trước\s*)?"
                       r"(?:ngày\s*)?$", re.IGNORECASE)
_RANGE = re.compile(r"từ\s*" + _DATE_TEXT + r"\s*(?:đến|tới|-|–)\s*(?:hết\s*)?" + _DATE_TEXT, re.IGNORECASE)

_DIGITS = {"khong": 0, "mot": 1, "hai": 2, "ba": 3, "bon": 4, "tu": 4, "nam": 5, "lam": 5, "sau": 6, "bay": 7,
           "tam": 8, "chin": 9}
_SCALES = {"nghin": 1000, "ngan": 1000, "trieu": 1_000_000, "ty": 1_000_000_000, "ti": 1_000_000_000}
_NUMBER_WORDS = set(_DIGITS) | set(_SCALES) | {"muoi", "tram", "linh", "le", "chuc"}
_UNITS = {"dong", "chan", "nguoi", "ngay", "ban", "bo", "lan", "thanh", "vien", "ho", "don", "vi", "cai", "chiec",
          "gio", "tuan", "thang", "quyen", "to", "trang", "phut", "km", "ha", "kg", "tan", "lit", "phong", "xa",
          "phuong", "nhiem", "vu", "de", "tai", "du", "an", "doanh", "nghiep", "co", "quan", "so", "san", "pham",
          "hoc", "sinh", "tre", "em", "lop", "khoa", "cong", "trinh", "hop", "dong", "mo", "hinh", "diem", "tieu",
          "chi", "muc", "ty", "trieu", "nghin", "ngan", "le", "chan", "vnd", "usd", "phan", "tram"}
_NUMBER_WITH_WORDS = re.compile(r"(?<![\d.,/])(\d{1,3}(?:\.\d{3})+|\d+)\s*(đồng|VNĐ|VND|đ)?\s*\(\s*(?:bằng chữ\s*:?\s*)?"
                                r"([^()\d]{2,160}?)\s*\)", re.IGNORECASE)
_PHU_LUC_REF = re.compile(r"\bPhụ lục\s+(?:số\s+)?([IVXLC]+|\d{1,2})\b", re.IGNORECASE)
_PHU_LUC_HEAD = re.compile(r"^\s*phu luc\s*(?:so\s*)?([ivxlc]+|\d{1,2})?\b")
_DIEU_HEAD = re.compile(r"^\s*Điều\s+(\d{1,3})\s*[.:]")
_DIEU_REF = re.compile(r"(?<![Đđ])\bĐiều\s+(\d{1,3})\b")
_ROMAN_HEAD = re.compile(r"^\s*([IVX]{1,5})\.\s+\S")
_INTERNAL_TAIL = re.compile(r"^\s*(?:của\s+)?(?:Quyết định|Quy chế|Quy định|Nghị quyết|Thông tư|Kế hoạch|Chỉ thị|"
                            r"Văn bản|Hướng dẫn|Điều lệ)\s+này\b", re.IGNORECASE)
_OTHER_DOC = re.compile(r"^.{0,40}?\b(của|tại|theo)?\s*(Luật|Bộ luật|Nghị định|Thông tư|Nghị quyết|Quyết định|Hiến pháp|"
                        r"Pháp lệnh|Chỉ thị|Kế hoạch|Công văn|Văn bản)\b|^.{0,40}?\d+/(\d{4}/)?[A-ZĐ]", re.IGNORECASE)
_BODY_ROLES = {"body", "list", "heading", "phu_luc_ref", "kinh_gui", "body_table", "trich_yeu", "vv", "other"}


def roman(value: str) -> int | None:
    numerals = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100}
    value = value.lower()
    if not value or any(ch not in numerals for ch in value):
        return None
    total = 0
    for position, ch in enumerate(value):
        current = numerals[ch]
        following = numerals[value[position + 1]] if position + 1 < len(value) else 0
        total += -current if current < following else current
    return total


def _to_roman(value: int) -> str:
    output = ""
    for number, letters in ((10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")):
        while value >= number:
            output, value = output + letters, value - number
    return output


def words_to_number(text: str) -> int | None:
    """"một tỷ hai trăm linh năm triệu" → 1205000000. None nếu không phải cụm số đọc bằng chữ."""
    tokens = re.findall(r"[a-z]+", fold(text))
    trailing = 0
    while tokens and tokens[-1] not in _NUMBER_WORDS:   # bỏ đơn vị phía sau: đồng, chẵn, người…
        if tokens[-1] not in _UNITS:
            return None  # "(sau đây gọi tắt là …)", "(không thành lập Hội đồng)": không phải số đọc bằng chữ
        tokens.pop()
        trailing += 1
    if trailing > 3:
        return None
    if not tokens or tokens[0] not in _DIGITS | {"muoi": 0} or any(t not in _NUMBER_WORDS for t in tokens):
        return None
    if len(tokens) == 1 and tokens[0] in ("tu", "lam"):
        return None
    total, group, pending, seen_digit = 0, 0, None, False
    for position, token in enumerate(tokens):
        if token in _DIGITS:
            if token in ("tu", "lam") and (position == 0 or tokens[position - 1] != "muoi"):
                return None
            if pending is not None:
                return None  # hai chữ số liền nhau: không phải cách đọc số
            pending, seen_digit = _DIGITS[token], True
        elif token in ("muoi", "chuc"):
            group += (pending if pending is not None else 1) * 10
            pending = None
        elif token == "tram":
            group += (pending if pending is not None else 1) * 100
            pending = None
        elif token in ("linh", "le"):
            continue
        else:
            group += pending or 0
            pending = None
            scale = _SCALES[token]
            if scale == 1_000_000_000:
                total = total * scale if total and not group else (total + (group or 1)) * scale
            else:
                total += (group or 1) * scale
            group = 0
    if not seen_digit and "muoi" not in tokens:
        return None
    return total + group + (pending or 0)


def _date(day, month, year) -> dt.date | None:
    try:
        return dt.date(int(year), int(month), int(day))
    except ValueError:
        return None


def _fmt(date: dt.date) -> str:
    return date.strftime("%d/%m/%Y")


def _issue(group, rule, label, status, number, text, expected=None, current=None, hint=None) -> dict:
    return {"group": group, "rule": rule, "label": label, "status": status, "expected": expected, "current": current,
            "hint": hint, "paragraph": number, "excerpt": " ".join(text.split())[:120]}


def check_dates(number: int, text: str, draft_date: dt.date, draft_dated: bool) -> list[dict]:
    issues = []
    for match in _DATE_RE.finditer(text):
        day, month, year = (int(value) for value in match.groups())
        if not 1900 <= year <= 2200:
            continue
        date = _date(day, month, year)
        shown = " ".join(match.group(0).split())
        if date is None:
            issues.append(_issue("time", "invalid_date", "Ngày tháng hợp lệ", "fail", number, text,
                                 "Ngày có thật trên lịch", shown,
                                 hint=f"Tháng {month}/{year} không có ngày {day}." if 1 <= month <= 12 else
                                      f"Không có tháng {month}."))
            continue
        before = text[max(0, match.start() - 40):match.start()]
        after = text[match.end():match.end() + 25]
        weekday = _WEEKDAY_BEFORE.search(before) or _WEEKDAY_AFTER.search(after)
        if weekday:
            key = fold(weekday.group(1)).replace("thu", "").strip()
            expected_day = _WEEKDAYS.get(key)
            if expected_day is not None and expected_day != date.weekday():
                issues.append(_issue("time", "weekday", "Thứ trong tuần khớp ngày", "fail", number, text,
                                     f"{_fmt(date)} là {WEEKDAY_NAMES[date.weekday()]}",
                                     f"{' '.join(weekday.group(1).split())}, {shown}"))
        if _DEADLINE.search(before) and date < draft_date:
            issues.append(_issue("time", "deadline_past", "Thời hạn sau ngày văn bản", "warn" if draft_dated else "info",
                                 number, text, f"Sau {_fmt(draft_date)}", shown,
                                 hint=("Thời hạn yêu cầu đã qua so với ngày ký văn bản." if draft_dated else
                                       "Dự thảo chưa ghi ngày; đang so với ngày hôm nay.")))
    for match in _RANGE.finditer(text):
        start, end = _date(*match.groups()[:3]), _date(*match.groups()[3:])
        if start and end and end < start:
            issues.append(_issue("time", "range", "Khoảng thời gian", "fail", number, text,
                                 "Ngày kết thúc sau ngày bắt đầu", f"Từ {_fmt(start)} đến {_fmt(end)}"))
    return issues


def check_number_words(number: int, text: str) -> list[dict]:
    issues = []
    for match in _NUMBER_WITH_WORDS.finditer(text):
        words = match.group(3)
        value = words_to_number(words)
        if value is None:
            continue
        digits = int(match.group(1).replace(".", ""))
        if digits != value:
            issues.append(_issue("number", "number_words", "Số viết bằng số và bằng chữ", "fail", number, text,
                                 "Hai cách viết cùng một giá trị",
                                 f"{match.group(1)} ≠ “{' '.join(words.split())}” (= {value:,})".replace(",", ".")))
    return issues


def _other_document_follows(text: str, end: int) -> bool:
    """Cụm ngay sau (đến dấu ; hoặc .) nhắc văn bản khác: "Điều 4 Luật Khoa học…", "Phụ lục I Nghị định 30…"."""
    following = re.split(r"[;]|\.\s", text[end:end + 90], maxsplit=1)[0]
    return bool(_OTHER_DOC.match(following))


def check_internal(paragraphs: list[tuple[int, str, str]], tables: set[int] | None = None) -> list[dict]:
    """tables: số đoạn nằm trong bảng (bảng so sánh, biểu mẫu) — không dùng để dựng danh sách Điều/đề mục."""
    tables = tables or set()
    issues = []
    appendix_numbers: set[int] = set()
    appendix_any = False
    articles: list[tuple[int, int, str]] = []
    romans: list[tuple[int, int, str]] = []
    for number, text, role in paragraphs:
        folded = fold(text)
        head = _PHU_LUC_HEAD.match(folded)
        if head and len(text) < 160 and (role in ("phu_luc", "heading", "other") or text.strip().isupper()):
            appendix_any = True
            if head.group(1):
                value = int(head.group(1)) if head.group(1).isdigit() else roman(head.group(1))
                if value:
                    appendix_numbers.add(value)
        if number in tables:
            continue
        article = _DIEU_HEAD.match(text)
        if article and role != "phu_luc":
            articles.append((int(article.group(1)), number, text))
        heading = _ROMAN_HEAD.match(text)
        title = text[heading.end() - 1:] if heading else ""
        if heading and role in ("heading", "body", "list") and len(text) < 220 and title.upper() == title:
            value = roman(heading.group(1))
            if value:
                romans.append((value, number, text))
    for (prev, _, _), (cur, number, text) in zip(articles, articles[1:]):
        if cur != prev + 1 and cur != 1:  # Quy chế/Quy định kèm theo đánh số lại từ Điều 1
            issues.append(_issue("internal", "article_seq", "Đánh số Điều liên tục", "warn", number, text,
                                 f"Điều {prev + 1}", f"Điều {cur}"))
    for (prev, _, _), (cur, number, text) in zip(romans, romans[1:]):
        if cur == prev or cur > prev + 1:
            issues.append(_issue("internal", "roman_seq", "Đánh số đề mục La Mã liên tục", "warn", number, text,
                                 f"Mục {_to_roman(prev + 1)}", f"Mục {_to_roman(cur)} (sau mục {_to_roman(prev)})",
                                 hint="Nếu đề mục dùng đánh số tự động của Word thì đối chiếu lại trên bản in."))
    max_article = max((value for value, _, _ in articles), default=0)
    article_heads = {number: len(_DIEU_HEAD.match(text).group(0)) for _, number, text in articles}
    reported: set[tuple[str, int]] = set()
    missing: dict[int, tuple[str, int, str]] = {}
    for number, text, role in paragraphs:
        if role not in _BODY_ROLES:
            continue
        for match in _PHU_LUC_REF.finditer(text):
            if fold(text).startswith("phu luc") and len(text) < 160:
                continue  # chính là tiêu đề phụ lục
            raw = match.group(1)
            value = int(raw) if raw.isdigit() else roman(raw)
            around = fold(text[max(0, match.start() - 40):match.end() + 40])
            if not value or value in appendix_numbers or _other_document_follows(text, match.end()) \
                    or not re.search(r"kem theo|gui kem|dinh kem|chi tiet (tai|theo)|tai phu luc|theo phu luc|phu luc .{0,6}nay", around):
                continue
            missing.setdefault(value, (raw, number, text))
        if max_article >= 2:
            for match in _DIEU_REF.finditer(text, article_heads.get(number, 0)):
                value = int(match.group(1))
                # Chỉ dẫn chiếu nội bộ rõ ràng: "Điều 5 Quyết định này", "Điều 7 của Quy chế này".
                if value <= max_article or not _INTERNAL_TAIL.match(text[match.end():]):
                    continue
                if ("dieu", value) in reported:
                    continue
                reported.add(("dieu", value))
                issues.append(_issue("internal", "article_ref", "Điều được dẫn có trong văn bản", "fail", number, text,
                                     f"Văn bản có Điều 1–{max_article}", f"Điều {value}"))
    if missing:
        first_raw, number, text = missing[min(missing)]
        names = ", ".join(f"Phụ lục {raw}" for raw, _, _ in (missing[key] for key in sorted(missing)))
        if appendix_any:
            issues.append(_issue("internal", "appendix", "Phụ lục được dẫn có trong văn bản", "warn", number, text,
                                 "Có " + ", ".join(f"Phụ lục {n}" for n in sorted(appendix_numbers)) if appendix_numbers
                                 else "Phụ lục có đánh số", f"Không thấy {names}"))
        else:
            issues.append(_issue("internal", "appendix", "Phụ lục được dẫn có trong văn bản", "info", number, text,
                                 "Có trong tệp", f"Không thấy {names}",
                                 hint="Nếu phụ lục gửi kèm thành tệp riêng thì bỏ qua mục này."))
    return issues


def check(paragraphs: list[tuple[int, str, str]], draft_date: dt.date, draft_dated: bool,
          tables: set[int] | None = None) -> list[dict]:
    issues = []
    for number, text, role in paragraphs:
        if role not in _BODY_ROLES or not text.strip():
            continue
        issues.extend(check_dates(number, text, draft_date, draft_dated))
        issues.extend(check_number_words(number, text))
    issues.extend(check_internal(paragraphs, tables))
    return issues
