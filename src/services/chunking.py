from __future__ import annotations

import hashlib
import re
import uuid
from collections import defaultdict
from dataclasses import dataclass


@dataclass(frozen=True)
class StructuredChunk:
    text: str
    chunk_index: int
    section_path: str
    parent_chunk_id: str
    parent_text: str
    heading: str | None = None
    page_start: int | None = None
    page_end: int | None = None


@dataclass(frozen=True)
class _Segment:
    path: tuple[str, ...]
    text: str


_CHAPTER_RE = re.compile(r"^\s*(CHƯƠNG|Chương)\s+([IVXLCDM0-9]+)\b.*")
_SECTION_RE = re.compile(r"^\s*(MỤC|Mục)\s+([IVXLCDM0-9]+)\b.*")
_ARTICLE_RE = re.compile(r"^\s*(ĐIỀU|Điều)\s+(\d+[A-Za-z]?)\s*[.:]?\s*.*")
_CLAUSE_RE = re.compile(r"^\s*(\d+)\s*[.)]\s+\S+")


def normalize_document_ref(value: str | None) -> str:
    value = (value or "").replace("đ", "d").replace("Đ", "D")
    value = value.replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", "", value).upper()


def split_text(
    text: str,
    size: int,
    overlap: int,
    seps: tuple[str, ...] = ("\n\n", "\n", ". ", "; ", " "),
) -> list[str]:
    text = text.strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]
    separator = next((sep for sep in seps if sep in text), None)
    if separator is None:
        step = max(1, size - overlap)
        return [text[index:index + size].strip() for index in range(0, len(text), step)]

    output: list[str] = []
    buffer = ""
    for part in text.split(separator):
        candidate = f"{buffer}{separator}{part}" if buffer else part
        if len(candidate) <= size:
            buffer = candidate
            continue
        if buffer.strip():
            output.append(buffer.strip())
        carry = output[-1][-overlap:] if output and overlap else ""
        buffer = f"{carry}{separator}{part}".strip()
        if len(buffer) > size:
            output.extend(split_text(buffer, size, overlap, seps[1:]))
            buffer = ""
    if buffer.strip():
        output.append(buffer.strip())
    return [chunk for chunk in output if chunk]


def _segments(text: str) -> list[_Segment]:
    path: dict[str, str | None] = {"chapter": None, "section": None, "article": None, "clause": None}
    current_path: tuple[str, ...] = ()
    buffer: list[str] = []
    output: list[_Segment] = []

    def path_tuple() -> tuple[str, ...]:
        return tuple(value for value in path.values() if value)

    def flush() -> None:
        nonlocal buffer
        body = "\n".join(buffer).strip()
        if body:
            output.append(_Segment(current_path or ("Nội dung",), body))
        buffer = []

    for raw_line in text.replace("\x00", " ").splitlines():
        line = raw_line.strip()
        if not line:
            if buffer and buffer[-1] != "":
                buffer.append("")
            continue

        chapter = _CHAPTER_RE.match(line)
        section = _SECTION_RE.match(line)
        article = _ARTICLE_RE.match(line)
        clause = _CLAUSE_RE.match(line) if path["article"] else None
        if chapter or section or article or clause:
            flush()
            if chapter:
                path.update(chapter=line, section=None, article=None, clause=None)
            elif section:
                path.update(section=line, article=None, clause=None)
            elif article:
                path.update(article=line, clause=None)
            elif clause:
                path.update(clause=f"Khoản {clause.group(1)}")
            current_path = path_tuple()
        elif not current_path:
            current_path = path_tuple()
        buffer.append(line)
    flush()
    return output


def _parent_path(path: tuple[str, ...]) -> tuple[str, ...]:
    for index, part in enumerate(path):
        if part.lower().startswith("điều "):
            return path[:index + 1]
    return path[:-1] if len(path) > 1 else path


def build_structured_chunks(
    text: str,
    size: int,
    overlap: int,
    doc_key: str,
    max_parent_chars: int = 8000,
) -> list[StructuredChunk]:
    segments = _segments(text)
    grouped: dict[tuple[str, ...], list[str]] = defaultdict(list)
    for segment in segments:
        grouped[_parent_path(segment.path)].append(segment.text)

    output: list[StructuredChunk] = []
    chunk_index = 0
    namespace = uuid.UUID("37c24870-ef50-44db-a789-8bada51dc46e")
    for segment in segments:
        parent_path = _parent_path(segment.path)
        parent_text = "\n\n".join(grouped[parent_path])[:max_parent_chars]
        stable_key = f"{doc_key}:{' > '.join(parent_path)}"
        parent_id = str(uuid.uuid5(namespace, stable_key))
        for chunk in split_text(segment.text, size=size, overlap=overlap):
            output.append(StructuredChunk(
                text=chunk,
                chunk_index=chunk_index,
                section_path=" > ".join(segment.path),
                parent_chunk_id=parent_id,
                parent_text=parent_text,
                heading=segment.path[-1] if segment.path else None,
            ))
            chunk_index += 1
    return output


def stable_point_id(collection: str, doc_id: int, chunk_index: int, text: str) -> str:
    digest = hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()[:16]
    namespace = uuid.UUID("f716c29e-49aa-45f8-9c87-a97a10c274c5")
    return str(uuid.uuid5(namespace, f"{collection}:{doc_id}:{chunk_index}:{digest}"))
