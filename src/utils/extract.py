"""Trích xuất văn bản với vòng đời tài nguyên được giới hạn rõ ràng."""
from __future__ import annotations

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


def extract_excel(path: str):
    output = []
    extension = os.path.splitext(path)[1].lower()
    engine = "xlrd" if extension == ".xls" else "openpyxl"
    with pd.ExcelFile(path, engine=engine) as workbook:
        for sheet_name in workbook.sheet_names:
            frame = pd.read_excel(workbook, sheet_name=sheet_name, dtype=str, keep_default_na=False)
            if frame.empty:
                continue
            columns = " | ".join(str(column) for column in frame.columns)
            for number, row in enumerate(frame.itertuples(index=False, name=None), 2):
                values = " | ".join(str(value) for value in row)
                output.append(f"\nSheet: {sheet_name}; cột: {columns}\nDòng {number}: {values}\n")
    return "".join(output).strip(), extension.lstrip(".")


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
