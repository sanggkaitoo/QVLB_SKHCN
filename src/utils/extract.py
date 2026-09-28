"""Trích xuất văn bản với vòng đời tài nguyên được giới hạn rõ ràng."""
from __future__ import annotations

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
    try:
        with tempfile.TemporaryDirectory(prefix="qlvb_convert_") as directory:
            subprocess.run(
                ["libreoffice", "--headless", "--convert-to", "docx", path, "--outdir", directory],
                capture_output=True,
                timeout=180,
                check=False,
            )
            docx_path = os.path.join(directory, os.path.basename(path) + "x")
            if os.path.exists(docx_path):
                text, _ = extract_docx(docx_path)
                return text, "doc_libre"
    except Exception as exc:
        print(f"  ! lỗi chuyển DOC: {exc}")
    return "", "doc_failed"


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
    try:
        with Image.open(path) as image:
            text = pytesseract.image_to_string(image, lang="vie")
        return text.strip(), "ocr_tesseract"
    except Exception as exc:
        print(f"  ! lỗi OCR ảnh: {exc}")
        return "", "ocr_failed"


def extract(path: str):
    extension = path.lower().rsplit(".", 1)[-1]
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
        if extension in ("png", "jpg", "jpeg", "bmp", "tiff"):
            return extract_image(path)
    except Exception as exc:
        print(f"  ! lỗi trích xuất {path}: {exc}")
    return "", "unsupported"
