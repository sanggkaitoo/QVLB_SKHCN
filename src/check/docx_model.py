"""Đọc .docx trực tiếp từ XML và tính định dạng THỰC TẾ của từng đoạn.

Word lưu định dạng theo nhiều tầng; giá trị hiệu lực của mỗi thuộc tính (tính độc lập) là:
    mặc định tài liệu (docDefaults) → style đoạn (theo chuỗi basedOn; không khai báo thì dùng
    style mặc định "Normal") → style ký tự (rStyle) → định dạng trực tiếp.
Font có thể khai báo qua theme (minorHAnsi…) hoặc chỉ ở thuộc tính w:cs; thẻ rỗng như
<w:spacing/> không được ghi đè giá trị kế thừa. Bỏ sót các điểm này là nguồn báo lỗi giả chính.
"""
from __future__ import annotations

import re
import unicodedata
import zipfile
from collections import Counter
from dataclasses import dataclass, field

from lxml import etree

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
A = "http://schemas.openxmlformats.org/drawingml/2006/main"
R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PR = "http://schemas.openxmlformats.org/package/2006/relationships"
NS = {"w": W, "a": A}
TWIPS_PER_CM = 566.929
TWIPS_PER_MM = 56.6929


def _w(tag: str) -> str:
    return f"{{{W}}}{tag}"


def _int(value) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _on(element) -> bool | None:
    """Thuộc tính bật/tắt kiểu <w:b/>, <w:b w:val="0"/>."""
    if element is None:
        return None
    value = element.get(_w("val"))
    return value not in ("0", "false", "off")


class DocxError(ValueError):
    """Tệp không phải .docx hợp lệ (thông điệp hiển thị cho người dùng)."""


# ------------------------------------------------------------------ mô hình

@dataclass
class RunInfo:
    text: str
    font: str | None
    size: float | None          # pt
    bold: bool
    italic: bool
    color: str | None


@dataclass
class Paragraph:
    index: int                  # số thứ tự đoạn (1-based, theo thứ tự trong tài liệu, gồm cả đoạn trong bảng)
    text: str
    style_id: str | None
    style_name: str | None
    in_table: bool
    table_index: int | None     # bảng cấp ngoài cùng chứa đoạn
    cell: tuple[int, int] | None  # (hàng, cột) trong bảng cấp ngoài cùng
    section: int
    jc: str | None
    ind_left: int               # twips
    ind_right: int
    first_line: int             # twips; âm = treo (hanging)
    before: int                 # twips (1/20 pt)
    after: int
    line: int | None
    line_rule: str              # auto | exact | atLeast
    contextual: bool
    numbered: bool
    runs: list[RunInfo] = field(default_factory=list)
    has_page_field: bool = False
    unnormalized: bool = False  # chữ lưu ở dạng Unicode tổ hợp (dấu tách rời), không phải dựng sẵn
    role: str = "other"         # gán ở docx_structure

    @property
    def stripped(self) -> str:
        return self.text.strip()

    @property
    def empty(self) -> bool:
        return not self.text.strip()

    def excerpt(self, limit: int = 90) -> str:
        text = " ".join(self.text.split())
        return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"

    def _counter(self, key) -> Counter:
        counter: Counter = Counter()
        for run in self.runs:
            weight = len(run.text.strip())
            if weight:
                counter[key(run)] += weight
        return counter

    def fonts(self) -> Counter:
        return self._counter(lambda run: run.font)

    def sizes(self) -> Counter:
        return self._counter(lambda run: run.size)

    def main_size(self) -> float | None:
        sizes = self.sizes()
        return sizes.most_common(1)[0][0] if sizes else None

    def ratio(self, attribute: str) -> float:
        total = sum(len(run.text.strip()) for run in self.runs)
        if not total:
            return 0.0
        return sum(len(run.text.strip()) for run in self.runs if getattr(run, attribute)) / total


@dataclass
class Section:
    index: int
    width: int | None
    height: int | None
    orient: str
    margins: dict[str, int | None]
    title_page: bool
    header_ids: list[str]
    first_paragraph: int = 0
    last_paragraph: int = 0


@dataclass
class Document:
    paragraphs: list[Paragraph]
    sections: list[Section]
    headers: dict[str, list[Paragraph]]   # rId -> đoạn trong header
    table_count: int

    def nonempty(self) -> list[Paragraph]:
        return [p for p in self.paragraphs if not p.empty]


# ------------------------------------------------------------------ styles

class _Styles:
    def __init__(self, styles_root, theme_fonts: dict[str, str]):
        self.theme = theme_fonts
        self.by_id: dict[str, etree._Element] = {}
        self.default_paragraph: str | None = None
        self.names: dict[str, str] = {}
        self.doc_rpr = None
        self.doc_ppr = None
        if styles_root is None:
            return
        defaults = styles_root.find("w:docDefaults", NS)
        if defaults is not None:
            self.doc_rpr = defaults.find("w:rPrDefault/w:rPr", NS)
            self.doc_ppr = defaults.find("w:pPrDefault/w:pPr", NS)
        for style in styles_root.findall("w:style", NS):
            style_id = style.get(_w("styleId"))
            if not style_id:
                continue
            self.by_id[style_id] = style
            name = style.find("w:name", NS)
            self.names[style_id] = name.get(_w("val")) if name is not None else style_id
            if style.get(_w("type")) == "paragraph" and style.get(_w("default")) in ("1", "true"):
                self.default_paragraph = style_id

    def chain(self, style_id: str | None) -> list:
        """Style và các style cha, từ gốc đến ngọn."""
        chain, seen = [], set()
        while style_id and style_id in self.by_id and style_id not in seen:
            seen.add(style_id)
            style = self.by_id[style_id]
            chain.append(style)
            based = style.find("w:basedOn", NS)
            style_id = based.get(_w("val")) if based is not None else None
        return list(reversed(chain))

    def paragraph_layers(self, style_id: str | None) -> list:
        """Các tầng pPr/rPr từ thấp đến cao cho một đoạn (chưa gồm định dạng trực tiếp)."""
        return self.chain(style_id or self.default_paragraph)

    def resolve_font(self, fonts: dict[str, str | None]) -> str | None:
        for key in ("hAnsi", "ascii", "eastAsia", "cs"):
            value = fonts.get(key)
            if value:
                return value
        return None


def _theme_fonts(root) -> dict[str, str]:
    fonts = {}
    if root is None:
        return fonts
    for kind in ("minorFont", "majorFont"):
        latin = root.find(f".//a:{kind}/a:latin", NS)
        if latin is not None and latin.get("typeface"):
            fonts["minor" if kind == "minorFont" else "major"] = latin.get("typeface")
    return fonts


def _apply_rpr(state: dict, rpr, theme: dict[str, str]) -> None:
    """Ghi đè từng thuộc tính chữ có khai báo trong rPr lên state."""
    if rpr is None:
        return
    fonts = rpr.find("w:rFonts", NS)
    if fonts is not None:
        for key in ("ascii", "hAnsi", "eastAsia", "cs"):
            value = fonts.get(_w(key))
            theme_ref = fonts.get(_w(key + "Theme")) or (fonts.get(_w("cstheme")) if key == "cs" else None)
            if theme_ref:
                value = theme.get("major" if theme_ref.startswith("major") else "minor", value)
            if value:
                state["fonts"][key] = value
    for tag, key in (("sz", "size"), ("szCs", "size_cs")):
        element = rpr.find(f"w:{tag}", NS)
        if element is not None and _int(element.get(_w("val"))):
            state[key] = _int(element.get(_w("val"))) / 2
    for tag, key in (("b", "bold"), ("i", "italic")):
        value = _on(rpr.find(f"w:{tag}", NS))
        if value is not None:
            state[key] = value
    color = rpr.find("w:color", NS)
    if color is not None and color.get(_w("val")):
        state["color"] = color.get(_w("val"))
    caps = _on(rpr.find("w:caps", NS))
    if caps is not None:
        state["caps"] = caps


def _apply_ppr(state: dict, ppr) -> None:
    """Ghi đè từng thuộc tính đoạn có khai báo; thẻ rỗng không ghi đè gì."""
    if ppr is None:
        return
    jc = ppr.find("w:jc", NS)
    if jc is not None and jc.get(_w("val")):
        state["jc"] = jc.get(_w("val"))
    ind = ppr.find("w:ind", NS)
    if ind is not None:
        for attrs, key in ((("left", "start"), "ind_left"), (("right", "end"), "ind_right")):
            for attr in attrs:
                value = _int(ind.get(_w(attr)))
                if value is not None:
                    state[key] = value
                    break
        first, hanging = _int(ind.get(_w("firstLine"))), _int(ind.get(_w("hanging")))
        if hanging is not None:
            state["first_line"] = -hanging
        elif first is not None:
            state["first_line"] = first
    spacing = ppr.find("w:spacing", NS)
    if spacing is not None:
        for attr, key in (("before", "before"), ("after", "after"), ("line", "line")):
            value = _int(spacing.get(_w(attr)))
            if value is not None:
                state[key] = value
        if spacing.get(_w("lineRule")):
            state["line_rule"] = spacing.get(_w("lineRule"))
        for attr, key in (("beforeAutospacing", "before"), ("afterAutospacing", "after")):
            if spacing.get(_w(attr)) in ("1", "true"):
                state[key] = 280  # Word dùng 14 pt cho "Auto"
    contextual = _on(ppr.find("w:contextualSpacing", NS))
    if contextual is not None:
        state["contextual"] = contextual
    if ppr.find("w:numPr/w:numId", NS) is not None:
        num = ppr.find("w:numPr/w:numId", NS).get(_w("val"))
        state["numbered"] = num not in (None, "0")


# ------------------------------------------------------------------ đọc tài liệu

def _read_xml(archive: zipfile.ZipFile, name: str):
    try:
        return etree.fromstring(archive.read(name), parser=etree.XMLParser(resolve_entities=False, huge_tree=True))
    except KeyError:
        return None


def _rels(archive: zipfile.ZipFile, name: str) -> dict[str, str]:
    root = _read_xml(archive, name)
    if root is None:
        return {}
    return {rel.get("Id"): rel.get("Target") for rel in root.findall(f"{{{PR}}}Relationship")}


def _split_lines(runs: list[RunInfo]) -> list[tuple[list[RunInfo], str]]:
    segments: list[tuple[list[RunInfo], list[str]]] = [([], [])]
    for run in runs:
        pieces = run.text.split("\n")
        for position, piece in enumerate(pieces):
            if position:
                segments.append(([], []))
            if piece:
                segments[-1][0].append(RunInfo(piece, run.font, run.size, run.bold, run.italic, run.color))
                segments[-1][1].append(piece)
    return [(seg_runs, "".join(parts)) for seg_runs, parts in segments if "".join(parts).strip()] or [(runs, "")]


class _Reader:
    def __init__(self, styles: _Styles):
        self.styles = styles
        self.paragraphs: list[Paragraph] = []
        self.sections: list[Section] = []
        self.table_count = 0
        self.number = 0            # số thứ tự đoạn XML (các dòng tách từ một đoạn dùng chung số)
        self._section = 0

    # --- văn bản và các run
    def _runs(self, container, base_r: dict) -> tuple[list[RunInfo], str, bool]:
        runs, parts, page_field = [], [], False
        for node in container.iter():
            tag = etree.QName(node).localname if isinstance(node.tag, str) else ""
            if tag == "instrText" and "PAGE" in (node.text or "").upper():
                page_field = True
            if tag == "fldSimple" and "PAGE" in (node.get(_w("instr")) or "").upper():
                page_field = True
            if tag != "r":
                continue
            # Bỏ chữ đã xoá (theo dõi thay đổi), bản dự phòng của đối tượng vẽ và chữ trong khung text box
            # (text box nằm trong một run; nội dung của nó không phải chữ của đoạn này).
            if any(etree.QName(parent).localname in ("del", "moveFrom", "Fallback", "txbxContent")
                   for parent in node.iterancestors() if isinstance(parent.tag, str)):
                continue
            text = []
            for child in node:
                name = etree.QName(child).localname
                if name == "t":
                    text.append(child.text or "")
                elif name == "tab":
                    text.append("\t")
                elif name in ("br", "cr"):
                    text.append("\n")
                elif name == "noBreakHyphen":
                    text.append("-")
            text = "".join(text)
            if not text:
                continue
            state = {**base_r, "fonts": dict(base_r["fonts"])}
            rpr = node.find("w:rPr", NS)
            if rpr is not None:
                rstyle = rpr.find("w:rStyle", NS)
                if rstyle is not None:
                    for layer in self.styles.chain(rstyle.get(_w("val"))):
                        _apply_rpr(state, layer.find("w:rPr", NS), self.styles.theme)
                _apply_rpr(state, rpr, self.styles.theme)
            if state.get("caps"):
                text = text.upper()
            runs.append(RunInfo(text=text, font=self.styles.resolve_font(state["fonts"]),
                                size=state.get("size") or state.get("size_cs"), bold=bool(state.get("bold")),
                                italic=bool(state.get("italic")), color=state.get("color")))
            parts.append(text)
        return runs, "".join(parts), page_field

    def paragraph(self, element, in_table: bool, table_index: int | None, cell) -> list[Paragraph]:
        ppr = element.find("w:pPr", NS)
        style_id = None
        if ppr is not None and ppr.find("w:pStyle", NS) is not None:
            style_id = ppr.find("w:pStyle", NS).get(_w("val"))
        p_state = {"jc": None, "ind_left": 0, "ind_right": 0, "first_line": 0, "before": 0, "after": 0,
                   "line": 240, "line_rule": "auto", "contextual": False, "numbered": False}
        r_state = {"fonts": {}, "size": 10.0, "size_cs": None, "bold": False, "italic": False, "color": None}
        _apply_ppr(p_state, self.styles.doc_ppr)
        _apply_rpr(r_state, self.styles.doc_rpr, self.styles.theme)
        for layer in self.styles.paragraph_layers(style_id):
            _apply_ppr(p_state, layer.find("w:pPr", NS))
            _apply_rpr(r_state, layer.find("w:rPr", NS), self.styles.theme)
        _apply_ppr(p_state, ppr)
        runs, text, page_field = self._runs(element, r_state)
        self.number += 1
        # Trong bảng, phần đầu và khối chữ ký hay dùng xuống dòng mềm (Shift+Enter) để xếp nhiều thành phần
        # vào một đoạn ("CỘNG HÒA… ↵ Độc lập…", "KT. GIÁM ĐỐC ↵ PHÓ GIÁM ĐỐC ↵ Nguyễn Văn A"): tách thành
        # từng dòng để nhận diện và kiểm tra riêng; các dòng giữ chung số thứ tự đoạn.
        segments = _split_lines(runs) if in_table and "\n" in text.strip("\n") else [(runs, text)]
        return [self._make(element, segment_runs, segment_text, style_id, p_state, in_table, table_index, cell,
                           page_field) for segment_runs, segment_text in segments]

    def _make(self, element, runs, text, style_id, p_state, in_table, table_index, cell, page_field) -> Paragraph:
        paragraph = Paragraph(
            index=self.number, text=unicodedata.normalize("NFC", text), style_id=style_id,
            style_name=self.styles.names.get(style_id or self.styles.default_paragraph or ""),
            in_table=in_table, table_index=table_index, cell=cell, section=self._section,
            jc=p_state["jc"], ind_left=p_state["ind_left"], ind_right=p_state["ind_right"],
            first_line=p_state["first_line"], before=p_state["before"], after=p_state["after"],
            line=p_state["line"], line_rule=p_state["line_rule"] or "auto", contextual=p_state["contextual"],
            numbered=p_state["numbered"], runs=runs, has_page_field=page_field,
            unnormalized=unicodedata.normalize("NFC", text) != text)
        return paragraph

    def block(self, container, in_table=False, table_index=None, cell=None) -> None:
        for child in container:
            tag = etree.QName(child).localname if isinstance(child.tag, str) else ""
            if tag == "p":
                self.paragraphs.extend(self.paragraph(child, in_table, table_index, cell))
                sect = child.find("w:pPr/w:sectPr", NS)
                if sect is not None:
                    self.close_section(sect)
            elif tag == "tbl":
                outer = table_index
                if not in_table:
                    self.table_count += 1
                    outer = self.table_count
                for row_number, row in enumerate(child.findall("w:tr", NS)):
                    for col_number, tc in enumerate(row.findall("w:tc", NS)):
                        self.block(tc, True, outer, cell if in_table else (row_number, col_number))
            elif tag in ("sdt", "customXml", "smartTag"):
                content = child.find("w:sdtContent", NS) if tag == "sdt" else child
                if content is not None:
                    self.block(content, in_table, table_index, cell)

    def close_section(self, sect) -> None:
        size, margins = sect.find("w:pgSz", NS), sect.find("w:pgMar", NS)
        width = _int(size.get(_w("w"))) if size is not None else None
        height = _int(size.get(_w("h"))) if size is not None else None
        orient = (size.get(_w("orient")) if size is not None else None) or (
            "landscape" if width and height and width > height else "portrait")
        first = self.sections[-1].last_paragraph + 1 if self.sections else 1
        self.sections.append(Section(
            index=len(self.sections) + 1, width=width, height=height, orient=orient,
            margins={side: _int(margins.get(_w(side))) if margins is not None else None
                     for side in ("top", "bottom", "left", "right")},
            title_page=_on(sect.find("w:titlePg", NS)) is True,
            header_ids=[ref.get(f"{{{R}}}id") for ref in sect.findall("w:headerReference", NS)
                        if ref.get(_w("type")) in (None, "default")],
            first_paragraph=first, last_paragraph=len(self.paragraphs)))
        self._section += 1


def read_docx(path: str) -> Document:
    try:
        archive = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise DocxError("Không đọc được tệp: không phải tệp .docx hợp lệ (có thể là .doc đổi đuôi hoặc tệp hỏng).") from exc
    with archive:
        document = _read_xml(archive, "word/document.xml")
        if document is None:
            raise DocxError("Tệp .docx thiếu phần nội dung (word/document.xml).")
        theme = _theme_fonts(_read_xml(archive, "word/theme/theme1.xml"))
        styles = _Styles(_read_xml(archive, "word/styles.xml"), theme)
        reader = _Reader(styles)
        body = document.find("w:body", NS)
        if body is None:
            raise DocxError("Tệp .docx không có nội dung.")
        reader.block(body)
        final = body.find("w:sectPr", NS)
        if final is not None or not reader.sections or reader.sections[-1].last_paragraph < len(reader.paragraphs):
            reader.close_section(final if final is not None else etree.Element(_w("sectPr")))
        # Header (số trang).
        rels = _rels(archive, "word/_rels/document.xml.rels")
        headers: dict[str, list[Paragraph]] = {}
        for section in reader.sections:
            for rid in section.header_ids:
                if rid in headers or rid not in rels:
                    continue
                root = _read_xml(archive, "word/" + rels[rid].lstrip("/").removeprefix("word/"))
                if root is None:
                    continue
                header_reader = _Reader(styles)
                header_reader.block(root)
                headers[rid] = header_reader.paragraphs
    return Document(paragraphs=reader.paragraphs, sections=reader.sections, headers=headers,
                    table_count=reader.table_count)


# ------------------------------------------------------------------ tiện ích

def fold(text: str) -> str:
    """Chữ thường, bỏ dấu, gộp khoảng trắng — dùng để so khớp mẫu câu ("Kính gửi", "Nơi nhận"…)."""
    text = unicodedata.normalize("NFD", text.lower()).replace("đ", "d")
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return re.sub(r"\s+", " ", text).strip()
