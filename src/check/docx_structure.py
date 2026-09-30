"""Nhận diện cấu trúc văn bản hành chính trong .docx và gán vai trò cho từng đoạn.

Vai trò chính:
  Phần đầu: quoc_hieu, tieu_ngu, co_quan, so_ky_hieu, dia_danh, ten_loai, trich_yeu, vv, tham_quyen, header_other
  Nội dung: kinh_gui, kinh_gui_item, body, heading, list, body_table, phu_luc_ref, empty
  Kết thúc: noi_nhan_title, noi_nhan_item, ky_quyen, chuc_vu, ky_ten, ky_ghi_chu, closing_other
  Phụ lục:  phu_luc

Điểm bắt đầu nội dung theo loại văn bản (không phụ thuộc hoàn toàn vào "Kính gửi"):
  - Có "Kính gửi" → từ dòng Kính gửi.
  - Quyết định, Nghị quyết, Chỉ thị, Quy chế, Quy định → từ "Căn cứ…" đầu tiên (hoặc sau trích yếu).
  - Loại khác có tên loại → sau tên loại và trích yếu.
  - Công văn không có Kính gửi → sau dòng "V/v" và phần đầu.
Kết thúc trước "Nơi nhận", khối chữ ký hoặc tiêu đề Phụ lục (kể cả khi nằm trong bảng).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from src.check.docx_model import Document, Paragraph, fold

TYPE_NAMES = {
    "NGHỊ QUYẾT": "NQ", "QUYẾT ĐỊNH": "QĐ", "CHỈ THỊ": "CT", "QUY CHẾ": "QC", "QUY ĐỊNH": "QyĐ",
    "THÔNG CÁO": "TC", "THÔNG BÁO": "TB", "HƯỚNG DẪN": "HD", "CHƯƠNG TRÌNH": "CTr", "KẾ HOẠCH": "KH",
    "PHƯƠNG ÁN": "PA", "ĐỀ ÁN": "ĐA", "DỰ ÁN": "DA", "BÁO CÁO": "BC", "BIÊN BẢN": "BB", "TỜ TRÌNH": "TTr",
    "HỢP ĐỒNG": "HĐ", "CÔNG ĐIỆN": "CĐ", "BẢN GHI NHỚ": "BGN", "BẢN THỎA THUẬN": "BTT",
    "GIẤY ỦY QUYỀN": "GUQ", "GIẤY MỜI": "GM", "GIẤY GIỚI THIỆU": "GGT", "GIẤY NGHỈ PHÉP": "GNP",
    "PHIẾU GỬI": "PG", "PHIẾU CHUYỂN": "PC", "PHIẾU BÁO": "PB",
}
TYPE_BY_FOLD = {fold(name): (name, code) for name, code in TYPE_NAMES.items()}
LEGAL_START = {"QĐ", "NQ", "CT", "QC", "QyĐ"}      # văn bản bắt đầu bằng căn cứ
TYPE_LABELS = {code: name.capitalize() for name, code in TYPE_NAMES.items()}
TYPE_LABELS["CV"] = "Công văn"

_SIGN_AUTHORITY = re.compile(r"^(kt|tl|tuq|q|tm)\s*\.")
_SIGN_TITLES = re.compile(r"^(pho )?(giam doc|chu tich|truong phong|chanh van phong|pho chanh van phong|"
                          r"thu truong|truong ban|vien truong|hieu truong|bi thu|cuc truong|tong giam doc|"
                          r"chanh thanh tra|truong ban quan ly|nguoi ky|thu ky)\b")
_DATE_LINE = re.compile(r"^[^\d,]{2,60},\s*ngay\b.*\bthang\b.*\bnam\b")
_SEPARATOR = re.compile(r"^[\s\-_–—=.*]+$")
_BULLET = re.compile(r"^\s*([-–—+•*·]|[a-zđ]\)|\d+(\.\d+)*[.)]|\(\d+\)|\([a-zđ]\))\s")
# Dòng ghi chú trong ngoặc về tài liệu kèm theo: "(Có phụ lục chi tiết kèm theo)", "(Dự thảo đính kèm)"…
_ATTACHMENT_NOTE = re.compile(r"^\(.*(kem theo|gui kem|dinh kem|phu luc|phu bieu|chi tiet tai|chi tiet theo|kem van ban).*\)\s*(\.|\./\.)?$")
_ROMAN_HEADING = re.compile(r"^\s*([IVXLC]+|[A-Z])\.\s")
_PART_HEADING = re.compile(r"^(phan|chuong|muc)\s+([ivxlc]+|\d+)\b")


@dataclass
class Structure:
    doc_type: str | None = None           # mã loại: QĐ, KH, CV…
    doc_type_label: str | None = None
    type_source: str | None = None        # tieu_de | ky_hieu | vv | kinh_gui
    start: int | None = None              # chỉ số (0-based) đoạn bắt đầu nội dung
    end: int | None = None                # chỉ số đoạn cuối nội dung (gồm)
    last_body: int | None = None          # đoạn nội dung cuối (kiểm tra "./.")
    closing_start: int | None = None
    appendix_start: int | None = None
    so_ky_hieu: int | None = None
    dia_danh: int | None = None
    party: bool = False
    uncertain: list[str] = field(default_factory=list)

    def describe(self, paragraphs: list[Paragraph]) -> dict:
        def point(index):
            if index is None:
                return None
            return {"index": paragraphs[index].index, "excerpt": paragraphs[index].excerpt(70)}
        return {"doc_type": self.doc_type, "doc_type_label": self.doc_type_label, "type_source": self.type_source,
                "start": point(self.start), "end": point(self.end), "last_body": point(self.last_body),
                "appendix": point(self.appendix_start), "party": self.party, "uncertain": self.uncertain}


def _f(paragraph: Paragraph) -> str:
    return fold(paragraph.text)


def _is_upper(text: str) -> bool:
    letters = [ch for ch in text if ch.isalpha()]
    return bool(letters) and sum(ch.isupper() for ch in letters) / len(letters) > 0.9


def _is_appendix_heading(paragraph: Paragraph) -> bool:
    text = _f(paragraph)
    if not (text.startswith("phu luc") or text.startswith("bieu ") or text.startswith("mau ")):
        return False
    if len(paragraph.stripped) > 160 or text.startswith("phu luc kem theo"):
        return False
    return _is_upper(paragraph.stripped[:30]) or paragraph.ratio("bold") > 0.5 or paragraph.jc == "center"


def _is_signature(paragraph: Paragraph) -> bool:
    text = _f(paragraph)
    if _SIGN_AUTHORITY.match(text):
        return True
    return (bool(_SIGN_TITLES.match(text)) and _is_upper(paragraph.stripped) and len(paragraph.stripped) < 70
            and (paragraph.in_table or paragraph.jc in ("center", "right", "end")))


def analyze(document: Document) -> Structure:
    ps = document.paragraphs
    info = Structure()
    n = len(ps)
    if not n:
        info.uncertain.append("Tài liệu không có nội dung.")
        return info
    head_limit = min(n, 80)

    info.party = any("dang cong san viet nam" in _f(p) for p in ps[:15])

    # --- điểm mốc phần đầu
    noi_nhan = next((i for i, p in enumerate(ps) if _f(p).startswith("noi nhan")), None)
    head_zone = range(0, min(head_limit, noi_nhan if noi_nhan is not None else n))
    for i in head_zone:
        p, text = ps[i], _f(ps[i])
        if not text:
            continue
        if "cong hoa xa hoi chu nghia viet nam" in text:
            p.role = "quoc_hieu"
        elif text.replace(" ", "").startswith("doclap-tudo-hanhphuc"):
            p.role = "tieu_ngu"
        elif re.match(r"^so\s*:?", text) and "/" in p.text and info.so_ky_hieu is None:
            p.role, info.so_ky_hieu = "so_ky_hieu", i
        elif _DATE_LINE.match(text) and info.dia_danh is None:
            p.role, info.dia_danh = "dia_danh", i

    title, title_key = None, None
    for i in head_zone:
        key = _f(ps[i]).rstrip(" :.")
        if key in TYPE_BY_FOLD and len(ps[i].stripped) < 40:
            title, title_key = i, key
            break
    if title is None:
        # Tên loại kèm phần mô tả trên cùng dòng: "BÁO CÁO THUYẾT MINH", "KẾ HOẠCH TỔ CHỨC…".
        for i in head_zone:
            p, key = ps[i], _f(ps[i])
            prefix = next((name for name in sorted(TYPE_BY_FOLD, key=len, reverse=True)
                           if key.startswith(name + " ")), None)
            if prefix and _is_upper(p.stripped) and len(p.stripped) < 160 and \
                    (p.jc == "center" or p.ratio("bold") > 0.5) and not p.in_table:
                title, title_key = i, prefix
                break
    vv = next((i for i in head_zone if re.match(r"^v\s*/\s*v\b", _f(ps[i]))), None)
    kinh_gui = next((i for i in head_zone if _f(ps[i]).startswith("kinh gui")), None)

    # --- loại văn bản
    if title is not None:
        name, code = TYPE_BY_FOLD[title_key]
        info.doc_type, info.type_source = code, "tieu_de"
        ps[title].role = "ten_loai"
    elif vv is not None or kinh_gui is not None:
        info.doc_type, info.type_source = "CV", "vv" if vv is not None else "kinh_gui"
    info.doc_type_label = TYPE_LABELS.get(info.doc_type) if info.doc_type else None
    if vv is not None:
        ps[vv].role = "vv"

    # --- cơ quan ban hành: các dòng phía trên "Số" trong cùng ô bảng (hoặc cùng cột đầu văn bản)
    if info.so_ky_hieu is not None:
        anchor = ps[info.so_ky_hieu]
        for p in ps[:info.so_ky_hieu]:
            same_cell = anchor.in_table and p.in_table and p.table_index == anchor.table_index and \
                p.cell and anchor.cell and p.cell[1] == anchor.cell[1]
            if (same_cell or not anchor.in_table) and not p.empty and p.role == "other" and \
                    not _SEPARATOR.match(p.text) and _is_upper(p.stripped):
                p.role = "co_quan"

    # --- trích yếu (sau tên loại)
    after_title = title
    if title is not None:
        j, count = title + 1, 0
        while j < n and count < 4:
            p = ps[j]
            if p.empty or _SEPARATOR.match(p.text):
                if count:
                    break
                j += 1
                continue
            text = _f(p)
            if text.startswith(("can cu", "kinh gui")) or len(p.stripped) > 500:
                break
            if p.jc == "center" or p.ratio("bold") > 0.5:
                p.role, after_title, count = "trich_yeu", j, count + 1
                j += 1
                continue
            break

    # --- điểm bắt đầu nội dung
    start = None
    if kinh_gui is not None and (title is None or kinh_gui > title):
        start = kinh_gui
    elif title is not None:
        if info.doc_type in LEGAL_START:
            start = next((i for i in range(after_title + 1, min(n, after_title + 25))
                          if _f(ps[i]).startswith("can cu")), None)
            # Dòng thẩm quyền ban hành (vd "GIÁM ĐỐC SỞ …") nằm giữa trích yếu và căn cứ.
            for i in range(after_title + 1, start if start is not None else after_title + 1):
                if not ps[i].empty and _is_upper(ps[i].stripped):
                    ps[i].role = "tham_quyen"
        if start is None:
            start = next((i for i in range(after_title + 1, n)
                          if not ps[i].empty and not _SEPARATOR.match(ps[i].text) and ps[i].role == "other"), None)
    elif vv is not None:
        header_table = ps[vv].table_index if ps[vv].in_table else None
        start = next((i for i in range(vv + 1, n) if not ps[i].empty and ps[i].role == "other"
                      and not _SEPARATOR.match(ps[i].text)
                      and not (header_table and ps[i].in_table and ps[i].table_index == header_table)), None)
    if start is None:
        info.uncertain.append("Không xác định được điểm bắt đầu nội dung (không thấy Kính gửi, tên loại văn bản hay V/v).")
        return info
    info.start = start

    # --- điểm kết thúc nội dung
    end_marker = None
    for i in range(start + 1, n):
        p = ps[i]
        if p.empty:
            continue
        if _f(p).startswith("noi nhan") or _is_signature(p) or _is_appendix_heading(p):
            end_marker = i
            break
    if end_marker is None:
        info.uncertain.append("Không thấy Nơi nhận hoặc khối chữ ký; nội dung được xét đến hết tài liệu.")
        info.end = n - 1
    else:
        info.end = end_marker - 1
        info.closing_start = end_marker

    # --- phụ lục: tiêu đề Phụ lục/Biểu/Mẫu sau khối kết thúc
    if info.closing_start is not None:
        info.appendix_start = next((i for i in range(info.closing_start, n)
                                    if not ps[i].empty and _is_appendix_heading(ps[i])), None)

    _assign_body(ps, info)
    _assign_closing(ps, info)
    for i in range(info.appendix_start or n, n):
        ps[i].role = "phu_luc" if not ps[i].empty else "empty"
    for p in ps[:start]:
        if p.role == "other":
            p.role = "empty" if p.empty else "header_other"
    return info


def _assign_body(ps: list[Paragraph], info: Structure) -> None:
    start, end = info.start, info.end
    kinh_gui = ps[start] if _f(ps[start]).startswith("kinh gui") else None
    i = start
    if kinh_gui is not None:
        kinh_gui.role = "kinh_gui"
        i = start + 1
        # Danh sách nơi gửi: cùng bảng với "Kính gửi" hoặc các dòng gạch đầu dòng ngắn ngay sau đó.
        while i <= end:
            p = ps[i]
            same_table = kinh_gui.in_table and p.in_table and p.table_index == kinh_gui.table_index
            if same_table or (not p.empty and p.stripped[:1] in "-–+" and len(p.stripped) < 160) or \
                    (p.empty and same_table):
                p.role = "kinh_gui_item" if not p.empty else "empty"
                i += 1
                continue
            break
    last_body = None
    for j in range(i, end + 1):
        p = ps[j]
        text = _f(p)
        if p.empty or _SEPARATOR.match(p.text):
            p.role = "empty"
        elif p.in_table:
            p.role = "body_table"
        elif _DATE_LINE.match(text) and len(p.stripped) < 80:
            p.role = "dia_danh_body"   # dòng địa danh, ngày tháng đặt trong thân văn bản (không áp quy tắc đoạn)
        elif _ATTACHMENT_NOTE.match(text) and len(p.stripped) < 300:
            p.role = "phu_luc_ref"
        elif re.match(r"^\(?\s*(kem theo|gui kem|phu luc kem theo|phu luc so|kem theo phu luc)", text) or \
                re.search(r"chi tiet .{0,80}phu luc", text) and len(p.stripped) < 200:
            p.role = "phu_luc_ref"
        elif (p.ratio("bold") >= 0.8 and len(p.stripped) <= 200) or _PART_HEADING.match(text) or \
                (_ROMAN_HEADING.match(p.stripped) and len(p.stripped) <= 200):
            p.role = "heading"
        elif p.numbered or _BULLET.match(p.text):
            p.role = "list"
        else:
            p.role = "body"
        if p.role in ("body", "list", "heading") and not p.in_table:
            last_body = j
    info.last_body = last_body


def _assign_closing(ps: list[Paragraph], info: Structure) -> None:
    if info.closing_start is None:
        return
    stop = info.appendix_start if info.appendix_start is not None else len(ps)
    noi_nhan_cell = None
    signer_lines = []
    for i in range(info.closing_start, stop):
        p = ps[i]
        text = _f(p)
        if p.empty:
            p.role = "empty"
            continue
        if text.startswith("noi nhan"):
            p.role = "noi_nhan_title"
            noi_nhan_cell = (p.table_index, p.cell[1]) if p.in_table and p.cell else ("free", None)
            continue
        in_noi_nhan = noi_nhan_cell is not None and (
            (p.in_table and p.cell and (p.table_index, p.cell[1]) == noi_nhan_cell) or
            (noi_nhan_cell[0] == "free" and (p.stripped[:1] in "-–+" or text.startswith("luu"))))
        if in_noi_nhan:
            p.role = "noi_nhan_item"
        elif _SIGN_AUTHORITY.match(text):
            p.role = "ky_quyen"
        elif text.startswith("(") or "da ky" in text or "ky ten" in text or "dong dau" in text:
            p.role = "ky_ghi_chu"
        elif _is_upper(p.stripped):
            p.role = "chuc_vu"
        else:
            p.role = "closing_other"
            signer_lines.append(i)
    # Họ tên người ký: dòng chữ thường cuối cùng của khối ký, 2–6 từ.
    for i in reversed(signer_lines):
        words = ps[i].stripped.split()
        if 2 <= len(words) <= 6 and all(word[:1].isupper() for word in words):
            ps[i].role = "ky_ten"
            break
