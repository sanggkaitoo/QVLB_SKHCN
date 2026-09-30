"""Trích xuất văn bản với vòng đời tài nguyên được giới hạn rõ ràng."""
from __future__ import annotations

import datetime as dt
import logging
import os
import subprocess
import tempfile

import docx
from docx.text.paragraph import Paragraph
from docx.table import Table
import fitz
import pandas as pd
import pytesseract
from PIL import Image

from src.core import config

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {"pdf", "docx", "doc", "xlsx", "xls", "csv", "png", "jpg", "jpeg", "bmp", "tiff"}


class ExtractionError(RuntimeError):
    """A source file could not be converted into usable text."""


def extract_pdf(path: str):
    pages, used_ocr = [], False
    with fitz.open(path) as document:
        for number, page in enumerate(document, 1):
            text = page.get_text("text", sort=True).strip()
            # OCR only the pages that need it; never render the entire PDF in memory.
            large_raster = any(fitz.Rect(info["bbox"]).get_area() >= page.rect.get_area() * 0.3 for info in page.get_image_info())
            if len(text) < 50 or large_raster:
                pixmap = page.get_pixmap(dpi=200, colorspace=fitz.csRGB, alpha=False)
                with Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples) as image:
                    ocr = pytesseract.image_to_string(image, lang="vie", timeout=120).strip()
                if len(ocr) > len(text):
                    text = ocr
                used_ocr = True
            if text:
                pages.append(f"[[PAGE {number}]]\n{text}")
    return "\n\n".join(pages), "pdf_mixed_ocr" if used_ocr else "pdf_text"


def extract_docx(path: str):
    document = docx.Document(path)
    parts = []
    for element in document.element.body.iterchildren():
        if element.tag.endswith("}p"):
            parts.append(Paragraph(element, document).text)
        elif element.tag.endswith("}tbl"):
            table = Table(element, document)
            rows = [" | ".join(cell.text for cell in row.cells) for row in table.rows]
            if rows:
                header = rows[0]
                parts.extend(f"Bảng: {header}\nDòng: {row}" for row in rows[1:])
                if len(rows) == 1:
                    parts.append(header)
    return "\n".join(parts).strip(), "docx"


def extract_doc(path: str):
    with tempfile.TemporaryDirectory(prefix="qlvb_convert_") as directory:
        try:
            completed = subprocess.run(
                ["libreoffice", "--headless", "--convert-to", "docx", path, "--outdir", directory],
                capture_output=True,
                timeout=180,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ExtractionError(f"Không chuyển được DOC bằng LibreOffice: {type(exc).__name__}") from exc
        docx_path = os.path.join(directory, os.path.splitext(os.path.basename(path))[0] + ".docx")
        if not os.path.exists(docx_path):
            detail = (completed.stderr or b"").decode("utf-8", "ignore").strip()[:200]
            raise ExtractionError(f"LibreOffice không tạo được DOCX (mã {completed.returncode}) {detail}".strip())
        text, _ = extract_docx(docx_path)
        return text, "doc_libre"


def _cell_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Có" if value else "Không"
    if isinstance(value, float) and value.is_integer():
        value = int(value)
    if isinstance(value, dt.datetime):
        return value.date().isoformat() if value.time() == dt.time() else value.isoformat(sep=" ")
    if isinstance(value, dt.date):
        return value.isoformat()
    return " ".join(str(value).split())


_names_patched = False


def _tolerate_broken_defined_names() -> None:
    """Một số tệp có tên vùng / vùng in hỏng (vd "#N/A") khiến openpyxl từ chối mở cả tệp.
    Tên vùng không ảnh hưởng nội dung ô nên bỏ qua lỗi đó thay vì bỏ cả tệp."""
    global _names_patched
    if _names_patched:
        return
    from openpyxl.reader.workbook import WorkbookParser
    original = WorkbookParser.assign_names

    def assign_names(self):
        try:
            original(self)
        except Exception as exc:
            logger.info("Bỏ qua tên vùng không hợp lệ trong bảng tính: %s", exc)

    WorkbookParser.assign_names = assign_names
    _names_patched = True


def _excel_sheets(path: str, extension: str):
    """Sinh (tên sheet, iterator các dòng giá trị) — đọc tuần tự, không nạp cả sheet vào bộ nhớ."""
    if extension == ".xls":
        import xlrd
        book = xlrd.open_workbook(path, on_demand=True)
        try:
            for index in range(book.nsheets):
                sheet = book.sheet_by_index(index)
                yield sheet.name, (sheet.row_values(row) for row in range(sheet.nrows))
                book.unload_sheet(index)
        finally:
            book.release_resources()
        return
    import openpyxl
    _tolerate_broken_defined_names()
    book = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        for sheet in book.worksheets:
            yield sheet.title, sheet.iter_rows(values_only=True)
    finally:
        book.close()


def _sheet_lines(name: str, rows) -> list[str]:
    """Bảng tính -> dòng chữ. Bỏ dòng/ô trống ở cuối, dừng khi gặp một dải dòng trống dài
    (vùng "đã định dạng" kéo tới cuối sheet), nhắc lại tên cột theo từng nhóm dòng để mỗi đoạn
    khi cắt nhỏ vẫn biết cột nào là gì."""
    filled: list[tuple[int, list[str]]] = []  # (số dòng Excel, giá trị đã bỏ ô trống cuối)
    empty_run = 0
    for number, row in enumerate(rows, 1):
        values = [_cell_text(value) for value in row]
        while values and not values[-1]:
            values.pop()
        if not values:
            empty_run += 1
            if empty_run >= config.EXCEL_EMPTY_ROW_STOP:
                break
            continue
        empty_run = 0
        filled.append((number, values))
    if not filled:
        return []
    # Dòng tiêu đề cột: dòng đầu tiên đủ "rộng" trong các dòng đầu; các dòng trước nó là tên biểu.
    widest = max(sum(1 for value in values if value) for _, values in filled[:15])
    header_at = next(index for index, (_, values) in enumerate(filled[:15])
                     if sum(1 for value in values if value) >= max(2, widest / 2)) if widest > 1 else 0
    titles = [" ".join(value for value in values if value) for _, values in filled[:header_at]]
    header = [value or f"Cột {index}" for index, value in enumerate(filled[header_at][1], 1)]
    header_text = " | ".join(header)
    lines = [f"\nSheet: {name}" + (f" — {' / '.join(titles)}" if titles else "")]
    for position, (number, values) in enumerate(filled[header_at + 1:]):
        if position % config.EXCEL_HEADER_EVERY == 0:
            lines.append(f"Sheet: {name}; cột: {header_text}")
        lines.append(f"Dòng {number}: " + " | ".join(values))
    if len(filled) == header_at + 1:  # chỉ có một dòng: in nguyên dòng đó
        lines.append(header_text)
    return lines


def extract_excel(path: str):
    extension = os.path.splitext(path)[1].lower()
    output: list[str] = []
    for name, rows in _excel_sheets(path, extension):
        output.extend(_sheet_lines(name, rows))
    return "\n".join(output).strip(), extension.lstrip(".")


def extract_csv(path: str):
    last_error = None
    for encoding in ("utf-8-sig", "utf-8", "cp1258", "latin1"):
        try:
            frame = pd.read_csv(path, sep=None, engine="python", encoding=encoding)
            return frame.to_csv(index=False, sep="\t").strip(), "csv"
        except UnicodeDecodeError as exc:
            last_error = exc
    if last_error:
        raise last_error
    return "", "csv"


def extract_image(path: str):
    with Image.open(path) as image:
        text = pytesseract.image_to_string(image, lang="vie", timeout=120)
    return text.strip(), "ocr_tesseract"


def extract(path: str):
    """Trả (text, method). Lỗi được ném ra dưới dạng ExtractionError có nguyên nhân rõ ràng."""
    extension = path.lower().rsplit(".", 1)[-1]
    if extension not in SUPPORTED_EXTENSIONS:
        raise ExtractionError(f"Định dạng .{extension} chưa được hỗ trợ")
    try:
        if extension == "pdf":
            return extract_pdf(path)
        if extension == "docx":
            return extract_docx(path)
        if extension == "doc":
            return extract_doc(path)
        if extension in ("xlsx", "xls"):
            return extract_excel(path)
        if extension == "csv":
            return extract_csv(path)
        return extract_image(path)
    except ExtractionError:
        raise
    except Exception as exc:
        logger.warning("Lỗi trích xuất %s: %s", os.path.basename(path), exc)
        raise ExtractionError(f"Không trích xuất được {os.path.basename(path)}: {type(exc).__name__}: {exc}"[:500]) from exc
