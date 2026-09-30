"""Kiểm tra chính tả tiếng Việt bằng code (ngoại tuyến, không gửi văn bản ra ngoài).

- Từ điển âm tiết lấy từ danh sách từ của pyvi (~7.200 âm tiết). So khớp theo (phần chữ, dấu thanh)
  nên không phụ thuộc vị trí đặt dấu: "hoà"/"hòa", "thuỷ"/"thủy" đều đúng.
- Quy tắc ghép vần: ngh/gh/k chỉ đứng trước i, e, ê (k cả y); vần tắc c/ch/p/t chỉ mang dấu sắc hoặc nặng…
- Cặp từ hay viết sai (âm tiết đúng nhưng từ sai): "sử lý" → "xử lý"…
- Trình bày: thừa khoảng trắng, dấu câu, lặp từ, ngoặc không đóng, mã ký tự cũ (TCVN3/VNI).
Tên riêng viết hoa chỉ bị báo khi sai quy tắc ghép vần; chữ viết tắt, số, mã, URL được bỏ qua.
"""
from __future__ import annotations

import os
import re
import unicodedata
from collections import Counter
from functools import lru_cache

TONES = {"̀": "huyen", "́": "sac", "̃": "nga", "̉": "hoi", "̣": "nang"}
_TONE_NAMES = {"huyen": "huyền", "sac": "sắc", "nga": "ngã", "hoi": "hỏi", "nang": "nặng", "": "ngang"}
_STOP_FINAL = re.compile(r"(c|ch|p|t)$")
_TOKEN = re.compile(r"[^\W\d_]+", re.UNICODE)
_FOREIGN = set("fjwz")
_MASK = [
    re.compile(r"https?://\S+|www\.\S+", re.I),
    re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
    re.compile(r"\b\d+[\w.]*(?:/[\w.Đđ-]+)+"),          # số ký hiệu: 123/KH-UBND, 30/2020/NĐ-CP
    re.compile(r"\b[A-ZĐ]{1,}[\w]*(?:-[A-ZĐ][\w]*)+"),  # mã: SKHCN-CĐS, NĐ-CP
    re.compile(r"\bV/v\b", re.I),
]
REDUPLICATION_OK = {"từ", "ngày", "người", "nhà", "đời", "mãi", "đâu", "xa", "năm", "tháng", "lớp", "làng",
                    "nhiều", "ai", "gì", "cao", "thật", "rồi", "vừa", "đều", "dần", "chiều", "sáng", "khắp",
                    "hằng", "hàng", "từng", "mỗi"}
# Cặp âm tiết đều hợp lệ nhưng ghép sai chính tả (hay gặp trong văn bản hành chính).
COMMON_MISTAKES = {
    "sử lý": "xử lý", "xử lí": None, "sát nhập": "sáp nhập", "chuẩn đoán": "chẩn đoán", "xuất xắc": "xuất sắc",
    "suất sắc": "xuất sắc", "xắp xếp": "sắp xếp", "sắp sếp": "sắp xếp", "sáng lạng": "xán lạn",
    "chín mùi": "chín muồi", "dành giật": "giành giật", "bổ xung": "bổ sung", "sơ xuất": "sơ suất",
    "kỹ niệm": "kỷ niệm", "kỉ thuật": "kĩ thuật", "kỷ thuật": "kỹ thuật", "kỷ năng": "kỹ năng",
    "chỉnh chu": "chỉn chu", "vô hình chung": "vô hình trung", "tựu chung": "tựu trung", "cọ sát": "cọ xát",
    "trí mạng": "chí mạng", "xác xuất": "xác suất", "suất phát": "xuất phát", "năng xuất": "năng suất",
    "công xuất": "công suất", "hiệu xuất": "hiệu suất", "lãi xuất": "lãi suất", "sản suất": "sản xuất",
    "chuyền thống": "truyền thống", "trú trọng": "chú trọng", "trủ trì": "chủ trì", "chủ chương": "chủ trương",
    "trủ trương": "chủ trương", "dám sát": "giám sát", "dà soát": "rà soát", "giàng buộc": "ràng buộc",
    "dàng buộc": "ràng buộc", "dải ngân": "giải ngân", "giữ liệu": "dữ liệu", "dữ gìn": "giữ gìn",
    "tổ trức": "tổ chức", "tri tiết": "chi tiết", "cơ trế": "cơ chế", "xây rựng": "xây dựng", "sây dựng": "xây dựng",
    "triển khay": "triển khai", "chiển khai": "triển khai", "chuẩn mực": None, "sử phạt": "xử phạt",
    "sử dụng": None, "xử dụng": "sử dụng", "xắc xuất": "xác suất", "phong phanh": None, "đường sá": None,
    "yếu điểm": None, "tham quan": None, "dành cho": None, "giành cho": "dành cho", "rành mạch": None,
    "dành mạch": "rành mạch", "giành mạch": "rành mạch", "sứ mạng": None, "chấn chỉnh": None,
    "trấn chỉnh": "chấn chỉnh", "chấn trỉnh": "chấn chỉnh", "tinh giảm": "tinh giản", "biên chế": None,
    "đột xuất": None, "đột suất": "đột xuất", "đề xuất": None, "đề suất": "đề xuất", "xuất cấp": None,
    "giải trình": None, "dải trình": "giải trình", "rải ngân": "giải ngân", "chuẩn bị": None,
    "trẩn bị": "chuẩn bị", "kiểm soát": None, "kiểm xoát": "kiểm soát", "kết luận": None,
}
COMMON_MISTAKES = {key: value for key, value in COMMON_MISTAKES.items() if value}
_WORDS_FILE = "pyvi/models/words.txt"


def split_tone(syllable: str) -> tuple[str, str]:
    """('hoa', 'huyen') cho 'hòa'/'hoà' — phần chữ (giữ â ă ê ô ơ ư đ) và dấu thanh."""
    decomposed = unicodedata.normalize("NFD", syllable)
    tone = ""
    kept = []
    for ch in decomposed:
        if ch in TONES:
            tone = TONES[ch]
        else:
            kept.append(ch)
    return unicodedata.normalize("NFC", "".join(kept)), tone


def _ascii(text: str) -> str:
    text = unicodedata.normalize("NFD", text).replace("đ", "d").replace("Đ", "D")
    return "".join(ch for ch in text if unicodedata.category(ch) != "Mn")


@lru_cache(maxsize=1)
def dictionary() -> tuple[set, set, dict, Counter]:
    """(âm tiết, từ 2 âm tiết, chỉ mục không dấu → âm tiết, tần suất âm tiết)."""
    import pyvi
    path = os.path.join(os.path.dirname(os.path.dirname(pyvi.__file__)), _WORDS_FILE)
    syllables, bigrams, frequency = set(), set(), Counter()
    by_ascii: dict[str, set] = {}
    with open(path, encoding="utf-8") as stream:
        for line in stream:
            word = unicodedata.normalize("NFC", line.strip().lower())
            parts = word.replace("_", " ").split()
            if not parts:
                continue
            keys = [split_tone(part) for part in parts]
            for part, key in zip(parts, keys):
                syllables.add(key)
                frequency[key] += 1
                by_ascii.setdefault(_ascii(part), set()).add(part)
            if len(parts) == 2:
                bigrams.add(tuple(keys))
    return syllables, bigrams, by_ascii, frequency


def phonotactic_problem(syllable: str) -> tuple[str, str | None] | None:
    """Lỗi ghép vần chắc chắn sai + gợi ý (nếu suy ra được). None nếu hợp lệ."""
    base, tone = split_tone(syllable.lower())
    if _STOP_FINAL.search(base) and tone not in ("sac", "nang") and len(base) > 1 and \
            any(v in base for v in "aăâeêioôơuưy"):
        return f"Vần kết thúc bằng “{_STOP_FINAL.search(base).group(1)}” chỉ đi với dấu sắc hoặc nặng (đang là dấu {_TONE_NAMES[tone]})", None
    rules = [
        (r"^ngh(?![iíìỉĩịeéèẻẽẹêếềểễệ])", "ng", "“ngh” chỉ đứng trước i, e, ê"),
        (r"^ng(?=[iíìỉĩịeéèẻẽẹêếềểễệ])", "ngh", "Trước i, e, ê phải viết “ngh”"),
        (r"^gh(?![iíìỉĩịeéèẻẽẹêếềểễệ])", "g", "“gh” chỉ đứng trước i, e, ê"),
        (r"^g(?=[eéèẻẽẹêếềểễệ])", "gh", "Trước e, ê phải viết “gh”"),
        (r"^k(?![iíìỉĩịeéèẻẽẹêếềểễệyýỳỷỹỵh])", "c", "“k” chỉ đứng trước i, e, ê, y"),
        (r"^c(?=[iíìỉĩịeéèẻẽẹêếềểễệyýỳỷỹỵ])", "k", "Trước i, e, ê, y phải viết “k”"),
        (r"^q(?!u)", "qu", "“q” luôn đi với “u”"),
    ]
    lowered = syllable.lower()
    for pattern, replacement, message in rules:
        if re.search(pattern, lowered):
            fixed = re.sub(pattern, replacement, lowered, count=1)
            return message, fixed
    return None


def _masked(text: str) -> str:
    for pattern in _MASK:
        text = pattern.sub(lambda m: " " * len(m.group(0)), text)
    return text


def _suggest(word: str, prev: str | None, nxt: str | None) -> str | None:
    syllables, bigrams, by_ascii, frequency = dictionary()
    candidates = [c for c in by_ascii.get(_ascii(word.lower()), ()) if split_tone(c) != split_tone(word.lower())]
    if not candidates:
        return None
    def score(candidate):
        key = split_tone(candidate)
        bonus = 0
        if prev and (split_tone(prev.lower()), key) in bigrams:
            bonus += 100
        if nxt and (key, split_tone(nxt.lower())) in bigrams:
            bonus += 100
        return bonus + frequency[key]
    best = max(candidates, key=score)
    return best if score(best) >= 100 or len(candidates) == 1 else " / ".join(
        sorted(candidates, key=score, reverse=True)[:3])


def _issue(paragraph, category, severity, original, suggestion, message, start=None):
    return {"index": paragraph.index, "excerpt": paragraph.excerpt(), "category": category, "severity": severity,
            "original": original, "suggestion": suggestion, "message": message, "source": "code", "offset": start}


def check_paragraph(paragraph, custom_words: set[str], strict_only: bool = False) -> list[dict]:
    """Lỗi chính tả/trình bày trong một đoạn. strict_only: chỉ báo lỗi chắc chắn (dùng cho phụ lục)."""
    text = paragraph.text
    issues: list[dict] = []
    if paragraph.unnormalized:
        issues.append(_issue(paragraph, "unicode", "warn", None, None,
                             "Chữ có dấu lưu ở dạng Unicode tổ hợp (dấu tách rời), chưa phải Unicode dựng sẵn."))
    if re.search(r"[¸µ¶·¹¨»¾¼½Æ®©¬]{2,}", text):
        issues.append(_issue(paragraph, "unicode", "fail", None, None,
                             "Có ký tự của bảng mã cũ (TCVN3/VNI); cần chuyển sang Unicode."))
    syllables, *_ = dictionary()
    masked = _masked(text)
    tokens = [(m.group(0), m.start()) for m in _TOKEN.finditer(masked)]
    for position, (word, start) in enumerate(tokens):
        lower = word.lower()
        if len(word) < 2 and lower not in "aăâeêioôơuưy":
            continue
        if _FOREIGN & set(lower) or (word.isupper() and len(word) > 1) or \
                (any(c.isupper() for c in word[1:]) and not word.isupper()):
            continue
        if lower in custom_words:
            continue
        key = split_tone(lower)
        if key in syllables:
            continue  # có trong từ điển (kể cả từ mượn: internet, logic, chip…)
        if word.isascii() or not word.isalpha():
            continue  # từ nước ngoài / viết tắt không dấu (kpi, backup…), ký hiệu đơn vị (m², m³)
        problem = phonotactic_problem(word)
        prev = tokens[position - 1][0] if position else None
        nxt = tokens[position + 1][0] if position + 1 < len(tokens) else None
        if problem:
            message, fixed = problem
            suggestion = fixed if fixed and split_tone(fixed) in syllables else _suggest(word, prev, nxt)
            if word[0].isupper() and suggestion:
                suggestion = suggestion[:1].upper() + suggestion[1:]
            issues.append(_issue(paragraph, "spelling", "fail" if word[0].islower() else "warn", word, suggestion,
                                 message, start))
            continue
        if strict_only or word[0].isupper():
            continue
        issues.append(_issue(paragraph, "spelling", "warn", word, _suggest(word, prev, nxt),
                             "Không có trong từ điển tiếng Việt — có khả năng sai chính tả.", start))
    lowered = " ".join(w.lower() for w, _ in tokens)
    for wrong, right in COMMON_MISTAKES.items():
        for match in re.finditer(rf"(?<![\w]){re.escape(wrong)}(?![\w])", lowered):
            original = wrong
            issues.append(_issue(paragraph, "spelling", "fail", original, right, "Viết sai chính tả từ này."))
    if strict_only:
        return issues
    _, bigrams, _, _ = dictionary()
    # Lặp từ liền nhau — trừ từ láy ("song song") và hai từ ghép nối tiếp ("hành vi | vi phạm",
    # "chuyển đổi số | số 148").
    for position in range(len(tokens) - 1):
        (a, start), (b, start_b) = tokens[position], tokens[position + 1]
        if a.lower() != b.lower() or len(a) < 2 or a.lower() in REDUPLICATION_OK:
            continue
        if text[start + len(a):start_b].strip():
            continue  # có dấu câu/số ở giữa
        key = split_tone(a.lower())
        prev = split_tone(tokens[position - 1][0].lower()) if position else None
        nxt = split_tone(tokens[position + 2][0].lower()) if position + 2 < len(tokens) else None
        if (key, key) in bigrams or ((prev, key) in bigrams and (key, nxt) in bigrams) or \
                (prev, key) in bigrams and nxt is None or re.match(r"\s*\d", text[start_b + len(b):]):
            continue
        issues.append(_issue(paragraph, "repeat", "warn", f"{a} {b}", a, "Lặp từ.", start))
    # Khoảng trắng và dấu câu.
    stripped = text.strip()
    # Trong ô bảng, khoảng trắng liên tiếp thường dùng để căn chỉnh — không báo.
    if not paragraph.in_table and paragraph.role not in ("dia_danh", "so_ky_hieu", "ky_ghi_chu") and not re.search(r"[☐□■▢…]|\.{4,}|_{3,}", stripped):
        for match in re.finditer(r"(?<=\S) {2,}(?=\S)", stripped):
            before, after = stripped[match.start() - 1], stripped[match.end()]
            if after in "/;:,)" or before in "/:(":
                continue  # chỗ để trống số, ngày ("số      /TTr", "ngày     /9/2026") hoặc ô điền
            issues.append(_issue(paragraph, "space", "warn", _around(stripped, match), None,
                                 f"Thừa {len(match.group(0)) - 1} khoảng trắng."))
    for match in re.finditer(r"(?<=\w) +([,;:!?])(?!\w)|(?<=\w) +(\.)(?![\w./])", stripped):
        issues.append(_issue(paragraph, "punct", "warn", _around(stripped, match), None,
                             f"Không để khoảng trắng trước dấu “{match.group(1) or match.group(2)}”."))
    for match in re.finditer(r"[,;](?=[^\s\d”\")\]./-])", _masked(stripped)):
        issues.append(_issue(paragraph, "punct", "warn", _around(stripped, match), None,
                             f"Thiếu khoảng trắng sau dấu “{match.group(0)}”."))
    for match in re.finditer(r"\( +(?=\S)|(?<=\S) +\)", stripped):
        issues.append(_issue(paragraph, "punct", "warn", _around(stripped, match), None,
                             "Không để khoảng trắng ngay trong dấu ngoặc."))
    body = re.sub(r"^\s*([\wđ]{1,4}|[ivxlc]+)\)\s", " ", stripped, flags=re.I)  # bỏ ký hiệu mục "a)", "2)"
    if body.count("(") != body.count(")") and paragraph.role not in ("ky_ghi_chu",):
        issues.append(_issue(paragraph, "bracket", "warn", None, None, "Dấu ngoặc đơn mở và đóng không khớp."))
    return issues


def _around(text: str, match, width: int = 14) -> str:
    start, end = max(0, match.start() - width), min(len(text), match.end() + width)
    return ("…" if start else "") + text[start:end] + ("…" if end < len(text) else "")
