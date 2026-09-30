"""Quy tắc thể thức áp lên cấu trúc đã nhận diện, theo bộ thể thức (Nghị định 30, SKHCN, tùy chỉnh)."""
from __future__ import annotations

import copy
import datetime as dt
import difflib
import os
import re
import threading
from collections import OrderedDict

import yaml

from src.check.docx_model import TWIPS_PER_CM, TWIPS_PER_MM, Document, Paragraph, fold
from src.check.docx_structure import TYPE_LABELS, TYPE_NAMES, Structure

PROFILE_FILE = "config/check_profiles.yaml"
COPY_CODES = {"SY": "Bản sao y", "TrS": "Bản trích sao", "SL": "Bản sao lục"}
TYPE_CODES = set(TYPE_NAMES.values())
MIXED_CASE_CODES = {"QyĐ", "CTr", "TTr", "TrS"}
A4 = (11906, 16838)
_lock = threading.Lock()
_cache: dict = {"mtime": None, "profiles": None}


class ProfileError(ValueError):
    pass


# ------------------------------------------------------------------ bộ thể thức

def _merge(base: dict, extra: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in (extra or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict) and key not in ("units",):
            result[key] = _merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def load_profiles() -> dict[str, dict]:
    mtime = os.path.getmtime(PROFILE_FILE)
    with _lock:
        if _cache["mtime"] == mtime:
            return _cache["profiles"]
    with open(PROFILE_FILE, encoding="utf-8") as stream:
        raw = yaml.safe_load(stream) or {}
    profiles: dict[str, dict] = {}

    def build(key: str, seen=()) -> dict:
        if key in profiles:
            return profiles[key]
        if key in seen or key not in raw:
            raise ProfileError(f"Bộ thể thức '{key}' không hợp lệ trong {PROFILE_FILE}")
        spec = dict(raw[key])
        parent = spec.pop("inherits", None)
        profiles[key] = _merge(build(parent, (*seen, key)), spec) if parent else spec
        profiles[key]["key"] = key
        return profiles[key]

    for key in raw:
        build(key)
    with _lock:
        _cache.update(mtime=mtime, profiles=profiles)
    return profiles


def _number(value, low: float, high: float, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise ProfileError(f"{name}: giá trị không hợp lệ")
    if not low <= number <= high:
        raise ProfileError(f"{name}: phải trong khoảng {low}–{high}")
    return number


def custom_profile(spec: dict) -> dict:
    """Bộ thể thức do người dùng nhập (form trên giao diện), dựa trên một bộ có sẵn."""
    profiles = load_profiles()
    base = profiles.get(spec.get("base") or "skhcn") or profiles["skhcn"]
    profile = copy.deepcopy(base)
    margins = spec.get("margins_mm") or {}
    for side in ("top", "bottom", "left", "right"):
        if side in margins:
            profile["page"]["margins_mm"][side] = _number(margins[side], 5, 60, f"Lề {side}")
    if spec.get("font"):
        profile["font"] = str(spec["font"]).strip()[:60]
    body = profile["body"]
    if spec.get("size_pt"):
        body["size_pt"] = [_number(v, 6, 30, "Cỡ chữ") for v in spec["size_pt"]][:4]
    if spec.get("first_line_cm") is not None:
        body["first_line_cm"] = [_number(v, 0, 5, "Thụt dòng đầu") for v in spec["first_line_cm"]][:3]
    if spec.get("before_pt") is not None or spec.get("after_pt") is not None:
        body["spacing"] = {"mode": "exact", "before_pt": _number(spec.get("before_pt", 6), 0, 72, "Cách đoạn trước"),
                           "after_pt": _number(spec.get("after_pt", 6), 0, 72, "Cách đoạn sau")}
    if spec.get("line"):
        line = spec["line"]
        if line in ("single", "1.5", "double") or isinstance(line, (int, float)):
            value = {"single": "single", "1.5": 1.5, "double": 2.0}.get(line, line)
            body["line"] = {"min": value, "max": value}
    if spec.get("align") in ("justify", "left", "center"):
        body["align"] = spec["align"]
    closing = profile["closing"]
    for key in ("noi_nhan_title_pt", "noi_nhan_items_pt", "signer_pt"):
        if spec.get(key):
            closing[key] = [_number(v, 6, 30, key) for v in spec[key]][:4]
    if spec.get("end_mark") in ("required", "recommended", "off"):
        profile["end_mark"] = spec["end_mark"]
    if "agency" in spec:
        profile["reference"]["agency"] = (str(spec["agency"]).strip() or None) if spec["agency"] else None
    if isinstance(spec.get("units"), dict):
        profile["reference"]["units"] = {str(k).strip(): str(v).strip()[:120] for k, v in spec["units"].items()
                                         if str(k).strip()}
    profile.update(key="custom", label="Tùy chỉnh", description="Thông số do người dùng nhập.")
    return profile


def get_profile(key: str, custom: dict | None = None) -> dict:
    if key == "custom":
        return custom_profile(custom or {})
    profiles = load_profiles()
    if key not in profiles:
        raise ProfileError("Bộ thể thức không tồn tại.")
    return profiles[key]


# ------------------------------------------------------------------ kết quả

class Report:
    """Gom kết quả theo nhóm → quy tắc → giá trị hiện tại, để cùng một lỗi trên nhiều đoạn chỉ hiện một dòng."""

    def __init__(self):
        self.groups: OrderedDict[str, dict] = OrderedDict()

    def group(self, key: str, label: str) -> dict:
        return self.groups.setdefault(key, {"key": key, "label": label, "checks": OrderedDict()})

    def add(self, group: str, rule: str, label: str, status: str, expected: str, current: str | None = None,
            paragraph: Paragraph | None = None, where: str | None = None, hint: str | None = None) -> None:
        checks = self.groups[group]["checks"]
        key = (rule, status, current if status != "pass" else "")
        check = checks.get(key)
        if check is None:
            check = checks[key] = {"rule": rule, "label": label, "status": status, "expected": expected,
                                   "current": current if status != "pass" else None, "hint": hint,
                                   "count": 0, "items": [], "where": where}
        check["count"] += 1
        if paragraph is not None and len(check["items"]) < 200:
            check["items"].append({"index": paragraph.index, "excerpt": paragraph.excerpt()})

    def result(self) -> list[dict]:
        order = {"fail": 0, "warn": 1, "info": 2, "pass": 3}
        output = []
        for group in self.groups.values():
            checks = sorted(group["checks"].values(), key=lambda c: (order[c["status"]], c["rule"]))
            # Quy tắc đã có lỗi thì không hiện thêm dòng "đạt" riêng cho quy tắc đó (vẫn đếm số đoạn đạt).
            failing = {c["rule"] for c in checks if c["status"] in ("fail", "warn")}
            for check in checks:
                if check["status"] == "pass" and check["rule"] in failing:
                    check["hidden"] = True
            status = "pass"
            if any(c["status"] == "fail" for c in checks):
                status = "fail"
            elif any(c["status"] == "warn" for c in checks):
                status = "warn"
            elif checks and all(c["status"] == "info" for c in checks):
                status = "info"
            output.append({"key": group["key"], "label": group["label"], "status": status, "checks": checks})
        return output


# ------------------------------------------------------------------ tiện ích so sánh

def _cm(twips: int | None) -> str:
    return "—" if twips is None else f"{twips / TWIPS_PER_CM:.2f}".rstrip("0").rstrip(".").replace(".", ",") + " cm"


def _mm(twips: int | None) -> str:
    return "—" if twips is None else f"{twips / TWIPS_PER_MM:.1f}".rstrip("0").rstrip(".").replace(".", ",") + " mm"


def _pt(value: float | None) -> str:
    if value is None:
        return "không xác định"
    return (f"{value:g}".replace(".", ",")) + " pt"


def _range_text(spec, unit: str) -> str:
    if isinstance(spec, dict):
        return f"{spec['min']:g}–{spec['max']:g} {unit}".replace(".", ",")
    return f"{spec:g} {unit}".replace(".", ",")


def _in_range(value: float, spec, tolerance: float) -> bool:
    low, high = (spec["min"], spec["max"]) if isinstance(spec, dict) else (spec, spec)
    return low - tolerance <= value <= high + tolerance


def _allowed(values) -> list[float]:
    return [float(v) for v in (values if isinstance(values, list) else [values])]


def _size_ok(size: float | None, allowed) -> bool:
    return size is not None and any(abs(size - v) < 0.26 for v in _allowed(allowed))


def _sizes_text(allowed) -> str:
    return " hoặc ".join(_pt(v) for v in _allowed(allowed))


_ALIGN = {"both": "Căn đều hai lề", "distribute": "Căn đều hai lề", "center": "Căn giữa", "left": "Căn trái",
          "start": "Căn trái", "right": "Căn phải", "end": "Căn phải", None: "Căn trái (mặc định)"}


def _line_multiple(p: Paragraph, size: float | None) -> tuple[float | None, str]:
    if p.line is None:
        return 1.0, "Single"
    if p.line_rule in (None, "auto"):
        multiple = p.line / 240
        label = {1.0: "Single", 1.5: "1,5 lines", 2.0: "Double"}.get(round(multiple, 2), f"Multiple {multiple:g}".replace(".", ","))
        return multiple, label
    points = p.line / 20
    base = (size or 14) * 1.15            # chiều cao một dòng đơn của Times New Roman ≈ 1,15 × cỡ chữ
    kind = "Exactly" if p.line_rule == "exact" else "At least"
    return points / base, f"{kind} {points:g} pt".replace(".", ",")


def _line_value(value) -> float:
    return 1.0 if value == "single" else float(value)


# ------------------------------------------------------------------ khổ giấy, lề

def check_layout(document: Document, profile: dict, report: Report) -> list[dict]:
    """Kiểm tra từng section; các section giống nhau gộp thành một "mẫu bố cục"."""
    report.group("layout", "Khổ giấy và lề trang")
    margins_spec = profile["page"]["margins_mm"]
    patterns: OrderedDict = OrderedDict()
    for section in document.sections:
        key = (section.width, section.height, tuple(section.margins.get(s) for s in ("top", "bottom", "left", "right")))
        patterns.setdefault(key, []).append(section)
    pattern_list = []
    for number, (key, sections) in enumerate(patterns.items(), 1):
        width, height, _ = key
        first = sections[0]
        where = _section_range(sections, len(document.sections))
        items = []
        if width and height:
            portrait = (min(width, height), max(width, height))
            ok = abs(portrait[0] - A4[0]) <= 60 and abs(portrait[1] - A4[1]) <= 60
            size_text = f"{portrait[0] / TWIPS_PER_MM:.0f} × {portrait[1] / TWIPS_PER_MM:.0f} mm"
            items.append(("page_size", "Khổ giấy", "A4 (210 × 297 mm)", ok, "A4" if ok else size_text))
            if first.orient == "landscape":
                report.add("layout", "orient", "Hướng giấy", "info", "Dọc hoặc ngang", "Khổ ngang", where=where,
                           hint="Chỉ để lưu ý; trang ngang thường dùng cho bảng biểu, phụ lục.")
        else:
            items.append(("page_size", "Khổ giấy", "A4", False, "không khai báo"))
        for side, label in (("top", "Lề trên"), ("bottom", "Lề dưới"), ("left", "Lề trái"), ("right", "Lề phải")):
            value = first.margins.get(side)
            spec = margins_spec[side]
            ok = value is not None and _in_range(value / TWIPS_PER_MM, spec, 0.3)
            items.append((f"margin_{side}", label, _range_text(spec, "mm"), ok, _mm(value)))
        for rule, label, expected, ok, current in items:
            report.add("layout", rule, label, "pass" if ok else "fail", expected, current, where=where)
        pattern_list.append({"pattern": number, "sections": [s.index for s in sections], "where": where,
                             "fail": sum(1 for item in items if not item[3])})
    return pattern_list


def _section_range(sections, total: int) -> str:
    if total == 1:
        return "Toàn văn bản"
    indexes = [s.index for s in sections]
    ranges, start = [], indexes[0]
    for prev, cur in zip(indexes, indexes[1:] + [None]):
        if cur != prev + 1:
            ranges.append(f"{start}" if start == prev else f"{start}–{prev}")
            if cur is not None:
                start = cur
    return "Section " + ", ".join(ranges)


# ------------------------------------------------------------------ đoạn văn

def _check_size(report, group, p, allowed, label="Cỡ chữ", rule="size"):
    size = p.main_size()
    sizes = {s for s in p.sizes() if s is not None}
    bad = [s for s in sizes if not _size_ok(s, allowed)]
    current = ", ".join(_pt(s) for s in sorted(bad)) if bad else _pt(size)
    report.add(group, rule, label, "fail" if bad else "pass", _sizes_text(allowed), current, p)


def _check_style(report, group, p, spec: dict, prefix: str, rule: str):
    """Cỡ chữ / đậm / nghiêng / in hoa của một thành phần; mỗi thành phần có mã quy tắc riêng."""
    if "size_pt" in spec:
        _check_size(report, group, p, spec["size_pt"], f"{prefix} · Cỡ chữ", f"{rule}.size")
    if spec.get("bold") is not None:
        bold = p.ratio("bold") >= 0.6
        report.add(group, f"{rule}.bold", f"{prefix} · Chữ đậm", "pass" if bold == spec["bold"] else "fail",
                   "Đậm" if spec["bold"] else "Không đậm", "Đậm" if bold else "Không đậm", p)
    if spec.get("italic") is not None:
        italic = p.ratio("italic") >= 0.6
        report.add(group, f"{rule}.italic", f"{prefix} · Chữ nghiêng", "pass" if italic == spec["italic"] else "fail",
                   "Nghiêng" if spec["italic"] else "Đứng", "Nghiêng" if italic else "Đứng", p)
    if spec.get("upper"):
        letters = [c for c in p.stripped if c.isalpha()]
        upper = bool(letters) and all(c.isupper() for c in letters)
        report.add(group, f"{rule}.upper", f"{prefix} · In hoa", "pass" if upper else "fail", "In hoa toàn bộ",
                   "Có chữ thường", p)


def check_header(document: Document, structure: Structure, profile: dict, report: Report) -> None:
    report.group("header", "Phần đầu văn bản")
    spec = profile.get("header") or {}
    labels = {"quoc_hieu": "Quốc hiệu", "tieu_ngu": "Tiêu ngữ", "co_quan": "Tên cơ quan", "so_ky_hieu": "Dòng số, ký hiệu",
              "dia_danh": "Địa danh, ngày tháng", "ten_loai": "Tên loại văn bản", "trich_yeu": "Trích yếu",
              "vv": "Trích yếu công văn (V/v)"}
    found = set()
    for p in document.paragraphs:
        if p.role not in labels or p.empty:
            continue
        found.add(p.role)
        role_spec = dict(spec.get(p.role) or {})
        if p.role == "co_quan":
            # Chỉ dòng cuối (cơ quan ban hành) in đậm; dòng cơ quan chủ quản phía trên không đậm.
            role_spec.pop("bold", None)
        _check_style(report, "header", p, role_spec, labels[p.role], p.role)
        if p.role == "quoc_hieu" and "cong hoa xa hoi chu nghia viet nam" in fold(p.text):
            report.add("header", "qh_text", "Quốc hiệu · Nội dung", "pass" if p.stripped.isupper() else "fail",
                       "CỘNG HÒA XÃ HỘI CHỦ NGHĨA VIỆT NAM", p.excerpt(), p)
        if p.role == "tieu_ngu":
            normalized = re.sub(r"\s+", " ", p.stripped.replace("–", "-").replace("—", "-"))
            ok = normalized == "Độc lập - Tự do - Hạnh phúc"
            report.add("header", "tn_text", "Tiêu ngữ · Cách viết", "pass" if ok else "fail",
                       "Độc lập - Tự do - Hạnh phúc (gạch nối có cách chữ)", p.stripped, p)
    if not structure.party:
        for role in ("quoc_hieu", "tieu_ngu", "so_ky_hieu", "dia_danh"):
            if role not in found:
                report.add("header", f"missing_{role}", f"{labels[role]}", "warn", "Có trong văn bản",
                           "Không tìm thấy", hint="Có thể văn bản chưa có thành phần này hoặc trình bày khác mẫu.")


def check_body(document: Document, structure: Structure, profile: dict, report: Report) -> None:
    report.group("body", "Nội dung văn bản")
    body = profile["body"]
    tolerance_indent = 0.06 * TWIPS_PER_CM
    for i in range(structure.start, (structure.end or structure.start) + 1):
        p = document.paragraphs[i]
        role = p.role
        if role in ("empty", "body_table", "other", "dia_danh_body"):
            continue
        _check_size(report, "body", p, body["size_pt"])
        if role in ("heading", "kinh_gui_item"):
            continue
        # Căn lề.
        if role == "kinh_gui":
            if not p.in_table:
                ok = p.jc in ("center", "both", "distribute")
                report.add("body", "align_kg", "Căn lề dòng Kính gửi", "pass" if ok else "fail",
                           "Căn giữa hoặc căn đều", _ALIGN.get(p.jc, p.jc), p)
            continue
        if role == "phu_luc_ref":
            # "(Kèm theo …)" mở đầu phụ lục phải căn giữa; ghi chú tài liệu kèm theo trong thân văn bản
            # ("(Có phụ lục chi tiết kèm theo)") căn giữa hoặc căn đều đều được.
            centered_only = fold(p.text).lstrip("( ").startswith(("kem theo", "phu luc kem theo"))
            ok = p.jc == "center" or (not centered_only and p.jc in ("both", "distribute"))
            report.add("body", "align_ref", "Căn lề dòng ghi chú tài liệu kèm theo", "pass" if ok else "fail",
                       "Căn giữa" if centered_only else "Căn giữa hoặc căn đều", _ALIGN.get(p.jc, p.jc), p)
            continue
        want = body.get("align", "justify")
        ok = (p.jc in ("both", "distribute")) if want == "justify" else (p.jc in (want, "start" if want == "left" else want))
        report.add("body", "align", "Căn lề", "pass" if ok else "fail",
                   {"justify": "Căn đều hai lề (Justify)", "left": "Căn trái", "center": "Căn giữa"}[want],
                   _ALIGN.get(p.jc, p.jc), p)
        # Thụt lề trái/phải.
        for rule, label, value, spec in (("indent_left", "Thụt lề trái", p.ind_left, body.get("indent_left_cm", 0)),
                                         ("indent_right", "Thụt lề phải", p.ind_right, body.get("indent_right_cm", 0))):
            if role == "list" and rule == "indent_left":
                continue
            ok = abs(value - float(spec) * TWIPS_PER_CM) <= tolerance_indent
            report.add("body", rule, label, "pass" if ok else "fail", _cm(float(spec) * TWIPS_PER_CM), _cm(value), p)
        # Thụt dòng đầu (không áp dụng cho danh sách).
        if role == "body":
            allowed = _allowed(body["first_line_cm"])
            ok = any(abs(p.first_line - v * TWIPS_PER_CM) <= tolerance_indent for v in allowed)
            current = _cm(p.first_line) if p.first_line >= 0 else f"Treo {_cm(-p.first_line)}"
            report.add("body", "first_line", "Thụt dòng đầu", "pass" if ok else "fail",
                       " hoặc ".join(_cm(v * TWIPS_PER_CM) for v in allowed), current, p)
        _check_spacing(report, p, body["spacing"])
        _check_line(report, p, body["line"])


def _check_spacing(report: Report, p: Paragraph, spec: dict) -> None:
    before, after = p.before / 20, p.after / 20
    if spec.get("mode") == "exact":
        for rule, label, value, want in (("before", "Cách đoạn trước", before, spec["before_pt"]),
                                         ("after", "Cách đoạn sau", after, spec["after_pt"])):
            report.add("body", rule, label, "pass" if abs(value - want) <= 0.5 else "fail", _pt(want), _pt(value), p)
    else:
        minimum = spec.get("min_pt", 6)
        ok = before + after >= minimum - 0.5
        report.add("body", "between", "Khoảng cách giữa các đoạn", "pass" if ok else "fail",
                   f"Tổng trước + sau ≥ {_pt(minimum)}", f"Trước {_pt(before)}, sau {_pt(after)}", p)


def _check_line(report: Report, p: Paragraph, spec: dict) -> None:
    low, high = _line_value(spec["min"]), _line_value(spec["max"])
    multiple, label = _line_multiple(p, p.main_size())
    want = "Single" if low == high == 1.0 else ("1,5 lines" if low == high == 1.5 else
                                                f"Từ {'Single' if low == 1 else low} đến {str(high).replace('.', ',')} lines")
    if p.line_rule in (None, "auto"):
        ok = low - 0.02 <= multiple <= high + 0.02
        report.add("body", "line", "Giãn dòng", "pass" if ok else "fail", want, label, p)
    else:
        # Exactly/At least: quy đổi theo cỡ chữ; tương đương thì chỉ nhắc nên dùng kiểu chuẩn.
        ok = low - 0.1 <= multiple <= high + 0.1
        report.add("body", "line", "Giãn dòng", "warn" if ok else "fail", want, label, p,
                   hint="Đang đặt khoảng cách cố định; nên chọn Line spacing: " + ("Single" if low == high == 1.0 else want))


def check_end_mark(document: Document, structure: Structure, profile: dict, report: Report) -> None:
    mode = profile.get("end_mark", "off")
    if mode == "off" or structure.last_body is None:
        return
    p = document.paragraphs[structure.last_body]
    ok = p.stripped.endswith("./.")
    report.add("body", "end_mark", "Kết thúc nội dung bằng ./.", "pass" if ok else ("fail" if mode == "required" else "warn"),
               "Đoạn nội dung cuối kết thúc bằng ./.", p.stripped[-12:] if not ok else None, p)


def check_closing(document: Document, structure: Structure, profile: dict, report: Report) -> None:
    if structure.closing_start is None:
        return
    report.group("closing", "Nơi nhận và chữ ký")
    closing = profile["closing"]
    for p in document.paragraphs[structure.closing_start:structure.appendix_start]:
        if p.empty:
            continue
        if p.role == "noi_nhan_title":
            _check_size(report, "closing", p, closing["noi_nhan_title_pt"], "Chữ “Nơi nhận” · Cỡ chữ", "nn.size")
            ok = p.ratio("bold") >= 0.6 and p.ratio("italic") >= 0.6
            report.add("closing", "nn_style", "Chữ “Nơi nhận” · Kiểu chữ", "pass" if ok else "fail",
                       "Nghiêng, đậm", ("Đậm" if p.ratio("bold") >= 0.6 else "Không đậm") + ", " +
                       ("nghiêng" if p.ratio("italic") >= 0.6 else "đứng"), p)
        elif p.role == "noi_nhan_item":
            _check_size(report, "closing", p, closing["noi_nhan_items_pt"], "Danh sách nơi nhận · Cỡ chữ", "nn_items.size")
        elif p.role in ("chuc_vu", "ky_quyen"):
            _check_size(report, "closing", p, closing["signer_pt"], "Chức vụ người ký · Cỡ chữ", "cv.size")
            report.add("closing", "cv_bold", "Chức vụ người ký · Chữ đậm", "pass" if p.ratio("bold") >= 0.6 else "fail",
                       "Đậm", "Đậm" if p.ratio("bold") >= 0.6 else "Không đậm", p)
        elif p.role == "ky_ten":
            _check_size(report, "closing", p, closing["signer_pt"], "Họ tên người ký · Cỡ chữ", "kt.size")
            report.add("closing", "kt_bold", "Họ tên người ký · Chữ đậm", "pass" if p.ratio("bold") >= 0.6 else "fail",
                       "Đậm", "Đậm" if p.ratio("bold") >= 0.6 else "Không đậm", p)


_FONT_SUFFIXES = ("psmt", "ps", "mt", "bolditalic", "bold", "italic", "regular", "cyr", "(vietnamese)")


def font_key(name: str) -> str:
    """Tên họ phông để so sánh: "Times New Roman Bold", "TimesNewRomanPSMT" → "timesnewroman"."""
    key = re.sub(r"[\s\-_,]", "", name.lower())
    changed = True
    while changed:
        changed = False
        for suffix in _FONT_SUFFIXES:
            if key.endswith(suffix) and len(key) > len(suffix) + 3:
                key, changed = key[:-len(suffix)], True
    return key


def check_fonts(document: Document, profile: dict, report: Report) -> None:
    report.group("font", "Phông chữ và màu chữ (toàn văn bản)")
    want = profile["font"]
    for p in document.paragraphs:
        if p.empty:
            continue
        fonts = {f for f in p.fonts() if f}
        bad = sorted(f for f in fonts if font_key(f) != font_key(want))
        if fonts or bad:
            report.add("font", "font", "Phông chữ", "fail" if bad else "pass", want, ", ".join(bad) or None, p)
        colors = {run.color.upper() for run in p.runs if run.text.strip() and run.color
                  and run.color.upper() not in ("AUTO", "000000")}
        if colors:
            report.add("font", "color", "Màu chữ", "warn", "Màu đen", ", ".join("#" + c for c in sorted(colors)), p)


def check_page_number(document: Document, profile: dict, report: Report) -> None:
    if not profile.get("page_number"):
        return
    group = "layout"
    headers = [(section, document.headers.get(rid, [])) for section in document.sections for rid in section.header_ids]
    numbered = [(section, p) for section, paragraphs in headers for p in paragraphs if p.has_page_field]
    length = sum(len(p.text) for p in document.paragraphs)
    if not numbered:
        if length > 3500:
            report.add(group, "page_number", "Số trang", "warn", "Đánh số trang ở giữa lề trên (từ trang 2)",
                       "Chưa đánh số trang", hint="Văn bản dài nhiều trang cần đánh số trang.")
        return
    section, p = numbered[0]
    report.add(group, "page_number_align", "Số trang · Vị trí", "pass" if p.jc == "center" else "fail",
               "Canh giữa lề trên", _ALIGN.get(p.jc, p.jc))
    size = p.main_size()
    if size is not None:
        report.add(group, "page_number_size", "Số trang · Cỡ chữ", "pass" if _size_ok(size, [13, 14]) else "fail",
                   "13 hoặc 14 pt", _pt(size))
    first = document.sections[0]
    report.add(group, "page_number_first", "Số trang · Trang đầu", "pass" if first.title_page else "warn",
               "Không hiển thị số trang ở trang thứ nhất", "Trang đầu vẫn hiện số trang" if not first.title_page else None,
               hint="Bật “Different First Page” trong Header & Footer.")


# ------------------------------------------------------------------ số, ký hiệu và ngày tháng

_NUMBER_LINE = re.compile(r"^\s*(S[ốỐoO])\s*(:?)\s*(\d*)\s*/\s*(.*?)\s*$", re.IGNORECASE)
_DATE = re.compile(r"^\s*(?P<place>[^,]+?)\s*,\s*ngày\s*(?P<day>\S*?)\s*tháng\s*(?P<month>\S*?)\s*năm\s*(?P<year>\S*)",
                   re.IGNORECASE)


def check_reference(document: Document, structure: Structure, profile: dict, report: Report,
                    today: dt.date | None = None) -> None:
    report.group("reference", "Số, ký hiệu và ngày tháng")
    if structure.party:
        report.add("reference", "party", "Văn bản của tổ chức Đảng", "info", "—", "Bỏ qua",
                   hint="Văn bản Đảng trình bày theo quy định riêng, không áp dụng Nghị định 30.")
        return
    reference = profile.get("reference") or {}
    if structure.so_ky_hieu is not None:
        _check_number(document.paragraphs[structure.so_ky_hieu], structure, reference, report)
    if structure.dia_danh is not None:
        _check_date(document.paragraphs[structure.dia_danh], report, today or dt.date.today())


def _check_number(p: Paragraph, structure: Structure, reference: dict, report: Report) -> None:
    text = " ".join(p.text.split())
    match = _NUMBER_LINE.match(text)
    if not match:
        report.add("reference", "format", "Cấu trúc số, ký hiệu", "fail", "Số: …/…-…", text, p)
        return
    word, colon, number, code = match.groups()
    add = lambda rule, label, ok, expected, current=None, hint=None, level="fail": report.add(  # noqa: E731
        "reference", rule, label, "pass" if ok else level, expected, None if ok else current, p, hint=hint)
    add("colon", "Dấu hai chấm sau “Số”", bool(colon), "Số:", word)
    add("word_case", "Chữ “Số” viết thường", word[0].isupper() and word[1:].islower(), "Số", word, level="warn")
    if not number:
        report.add("reference", "number_blank", "Số thứ tự", "info", "Để trống cho văn thư cấp khi ban hành", "Để trống", p)
    else:
        add("number_pad", "Số nhỏ hơn 10 có số 0 phía trước", not (int(number) < 10 and len(number) == 1),
            "0" + number if len(number) == 1 else number, number)
    add("no_space", "Không có khoảng trắng trong ký hiệu", " " not in code, code.replace(" ", ""), code)
    code = code.replace(" ", "")
    parts = code.split("-")
    add("dash", "Dấu gạch nối giữa các nhóm", all(parts) and code, "Không thừa/thiếu dấu “-”", code)
    parts = [part for part in parts if part]
    if not parts:
        return
    doc_type = structure.doc_type
    first = parts[0]
    agency_index = 1
    if first in TYPE_CODES or first in COPY_CODES or first.upper() in {c.upper() for c in TYPE_CODES | set(COPY_CODES)}:
        exact = next((c for c in TYPE_CODES | set(COPY_CODES) if c.upper() == first.upper()), first)
        add("type_case", "Viết đúng ký hiệu loại văn bản", first == exact, exact, first,
            hint="Phụ lục III NĐ 30: QyĐ, CTr, TTr, TrS viết đúng chữ hoa/thường như quy định.")
        if doc_type and doc_type != "CV" and exact not in COPY_CODES:
            add("type_match", "Ký hiệu khớp tên loại văn bản", exact == doc_type,
                f"{TYPE_LABELS.get(doc_type, doc_type)} → {doc_type}", f"{exact} ({TYPE_LABELS.get(exact, exact)})")
        if doc_type == "CV":
            add("cv_type", "Công văn không mang ký hiệu loại", False, "…/CƠQUAN-ĐƠNVỊ", first,
                hint="Nếu đây không phải công văn, kiểm tra tên loại văn bản ở phần đầu.")
    else:
        agency_index = 0
        if first.upper() == "CV":
            add("cv_code", "Công văn không dùng ký hiệu CV", False, "…/CƠQUAN-ĐƠNVỊ", first)
            agency_index = 1
        elif doc_type and doc_type != "CV":
            add("type_match", "Ký hiệu khớp tên loại văn bản", False,
                f"{TYPE_LABELS.get(doc_type, doc_type)} → {doc_type}-…", first)
    if agency_index >= len(parts):
        add("agency_missing", "Có ký hiệu cơ quan ban hành", False, "…-CƠQUAN", code)
        return
    agency = parts[agency_index]
    expected_agency = reference.get("agency")
    if expected_agency:
        add("agency", "Ký hiệu cơ quan", agency == expected_agency, expected_agency, agency)
    else:
        add("agency_upper", "Ký hiệu cơ quan viết hoa", agency == agency.upper(), agency.upper(), agency)
    units = reference.get("units") or {}
    rest = parts[agency_index + 1:]
    if rest:
        unit = rest[0]
        if units:
            if unit in units:
                report.add("reference", "unit", "Mã đơn vị soạn thảo", "pass", "Thuộc danh mục", None, p,
                           hint=f"{unit}: {units[unit]}")
            else:
                exact = next((u for u in units if u.upper() == unit.upper()), None)
                close = exact or next(iter(difflib.get_close_matches(unit.upper(), [u.upper() for u in units], 1, 0.5)), None)
                suggestion = next((u for u in units if u.upper() == (close or "")), None)
                hint = f"Nên dùng {suggestion} – {units[suggestion]}." if suggestion else \
                    "Danh mục: " + ", ".join(units) + " (sửa trong config/check_profiles.yaml)."
                add("unit", "Mã đơn vị soạn thảo", False, "Mã trong danh mục đơn vị", unit, hint=hint)
        else:
            add("unit_upper", "Mã đơn vị viết hoa", unit == unit.upper(), unit.upper(), unit)
    elif doc_type == "CV" and units:
        add("unit_missing", "Công văn có mã đơn vị soạn thảo", False, f"{agency}-<mã phòng>", code, level="warn")


def _check_date(p: Paragraph, report: Report, today: dt.date) -> None:
    text = " ".join(p.text.split())
    match = _DATE.match(text)
    if not match:
        report.add("reference", "date_format", "Địa danh, ngày tháng", "fail", "<Địa danh>, ngày … tháng … năm …", text, p)
        return
    place, day, month, year = (match.group(k) for k in ("place", "day", "month", "year"))
    add = lambda rule, label, ok, expected, current=None, level="fail", hint=None: report.add(  # noqa: E731
        "reference", rule, label, "pass" if ok else level, expected, None if ok else current, p, hint=hint)
    add("place_case", "Viết hoa chữ cái đầu địa danh", place[:1].isupper(), place[:1].upper() + place[1:], place)
    if not day.strip("…. "):
        report.add("reference", "day_blank", "Ngày", "info", "Để trống cho văn thư ghi khi ký", "Để trống", p)
    elif day.isdigit():
        add("day_pad", "Ngày nhỏ hơn 10 có số 0 phía trước", not (int(day) < 10 and len(day) == 1), "0" + day, day)
        add("day_valid", "Ngày hợp lệ", 1 <= int(day) <= 31, "01–31", day)
    if not month.isdigit():
        add("month", "Tháng", False, "Số tháng (01, 02, 3 … 12)", month or "trống")
        return
    value = int(month)
    add("month_valid", "Tháng hợp lệ", 1 <= value <= 12, "1–12", month)
    if value in (1, 2):
        add("month_pad", "Tháng 1, 2 có số 0 phía trước", len(month) == 2, f"0{value}", month)
    elif len(month) == 2 and month.startswith("0"):
        add("month_pad", "Tháng 3 trở đi không thêm số 0", False, str(value), month, level="warn")
    if year.isdigit() and len(year) == 4:
        add("year_now", "Năm ban hành", int(year) == today.year, str(today.year), year, level="warn",
            hint="So với ngày hiện tại; bỏ qua nếu văn bản ban hành năm khác.")
        if int(year) == today.year:
            add("month_now", "Tháng ban hành", value == today.month, f"{today.month:02d}" if today.month < 3 else str(today.month),
                month, level="warn", hint="Tháng khác tháng hiện tại: kiểm tra lại nếu là dự thảo sẽ trình tháng này.")
    else:
        add("year", "Năm", False, "4 chữ số", year or "trống")
