"""Trích xuất văn bản với vòng đời tài nguyên được giới hạn rõ ràng."""
from __future__ import annotations

import os
import subprocess
import tempfile

import docx
import fitz
import pandas as pd
import pytesseract
from pdf2image import convert_from_path
from PIL import Image


def extract_pdf(path: str):
    text = ""
    try:
        with fitz.open(path) as document:
            text = "\n".join(page.get_text("text") for page in document)
    except Exception as exc:
        print(f"  ! lỗi đọc PDF text {path}: {exc}")
    text = text.strip()
    if len(text) >= 50:
        return text, "pdf_text"

    print("  > PDF scan, chạy OCR tiếng Việt...")
    images = []
    try:
        images = convert_from_path(path)
        ocr = "\n".join(pytesseract.image_to_string(image, lang="vie") for image in images)
        return ocr.strip(), "ocr_tesseract"
    except Exception as exc:
        print(f"  ! lỗi OCR PDF: {exc}")
        return "", "ocr_failed"
    finally:
        for image in images:
            image.close()


def extract_docx(path: str):
    document = docx.Document(path)
    parts = [paragraph.text for paragraph in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            parts.append("\t".join(cell.text for cell in row.cells))
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
    output = ""
    with pd.ExcelFile(path) as workbook:
        for sheet_name in workbook.sheet_names:
            frame = pd.read_excel(workbook, sheet_name=sheet_name)
            if frame.empty:
                continue
            output += (
                f"\n--- Sheet: {sheet_name} ---\n"
                + frame.to_csv(index=False, sep="\t")
                + "\n"
            )
    return output.strip(), "xlsx"


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
        if extension in ("png", "jpg", "jpeg", "bmp", "tiff"):
            return extract_image(path)
    except Exception as exc:
        print(f"  ! lỗi trích xuất {path}: {exc}")
    return "", "unsupported"
