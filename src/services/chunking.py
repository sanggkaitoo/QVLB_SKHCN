from __future__ import annotations

import hashlib
import re
import uuid
from collections import defaultdict
from dataclasses import dataclass

from src.services.document_fields import normalize_document_ref  # noqa: F401  (re-export)


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
    page: int | None = None


_CHAPTER_RE = re.compile(r"^\s*(CHƯƠNG|Chương)\s+([IVXLCDM0-9]+)\b.*")
_SECTION_RE = re.compile(r"^\s*(MỤC|Mục)\s+([IVXLCDM0-9]+)\b.*")
_ARTICLE_RE = re.compile(r"^\s*(ĐIỀU|Điều)\s+(\d+[A-Za-z]?)\s*[.:]?\s*.*")
_CLAUSE_RE = re.compile(r"^\s*(\d+)\s*[.)]\s+\S+")


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
    page = None

    def path_tuple() -> tuple[str, ...]:
        return tuple(value for value in path.values() if value)

    def flush() -> None:
        nonlocal buffer
        body = "\n".join(buffer).strip()
        if body:
            output.append(_Segment(current_path or ("Nội dung",), body, page))
        buffer = []

    for raw_line in text.replace("\x00", " ").splitlines():
        line = raw_line.strip()
        page_marker = re.fullmatch(r"\[\[PAGE (\d+)\]\]", line)
        if page_marker:
            flush()
            page = int(page_marker.group(1))
            continue
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
    tokenizer=None,
) -> list[StructuredChunk]:
    segments = _segments(text)
    grouped: dict[tuple[str, ...], list[str]] = defaultdict(list)
    for segment in segments:
        grouped[(_parent_path(segment.path), segment.page)].append(segment.text)
    parents = {key: "\n\n".join(parts) for key, parts in grouped.items()}

    output: list[StructuredChunk] = []
    chunk_index = 0
    namespace = uuid.UUID("37c24870-ef50-44db-a789-8bada51dc46e")
    offsets = defaultdict(int)
    for segment in segments:
        parent_path = _parent_path(segment.path)
        group_key = (parent_path, segment.page)
        full_parent = parents[group_key]
        segment_start = offsets[group_key]
        offsets[group_key] += len(segment.text) + 2
        pieces = token_windows(segment.text, tokenizer, size, overlap) if tokenizer else split_text(segment.text, size=size, overlap=overlap)
        search_start = 0
        for chunk in pieces:
            position = segment.text.find(chunk, search_start)
            if position < 0:
                position = segment.text.find(chunk)
            if position < 0:
                parent_text, start, end = chunk, 0, len(chunk)
            else:
                search_start = position + 1
                center = segment_start + position
                padding = max(0, (max_parent_chars - len(chunk)) // 2)
                start = max(0, center - padding)
                end = min(len(full_parent), center + len(chunk) + padding)
                parent_text = full_parent[start:end]
            stable_key = f"{doc_key}:{' > '.join(parent_path)}:{segment.page}:{start}:{end}"
            parent_id = str(uuid.uuid5(namespace, stable_key))
            output.append(StructuredChunk(
                text=chunk,
                chunk_index=chunk_index,
                section_path=" > ".join(segment.path),
                parent_chunk_id=parent_id,
                parent_text=parent_text,
                heading=segment.path[-1] if segment.path else None,
                page_start=segment.page,
                page_end=segment.page,
            ))
            chunk_index += 1
    return output


def token_windows(text, tokenizer, size, overlap):
    if size <= 0 or overlap < 0 or overlap >= size:
        raise ValueError("Chunk overlap must be smaller than size")
    offsets = tokenizer(text, add_special_tokens=False, return_offsets_mapping=True)["offset_mapping"]
    if not offsets:
        return []
    chunks = []
    start = 0
    while start < len(offsets):
        end = min(start + size, len(offsets))
        # Keep complete lines/rows where possible, without exceeding the token budget.
        if end < len(offsets):
            for boundary in range(end - 1, start + max(size // 2, overlap), -1):
                if "\n" in text[offsets[boundary - 1][1]:offsets[boundary][0]]:
                    end = boundary
                    break
        value = text[offsets[start][0]:offsets[end - 1][1]].strip()
        if value:
            chunks.append(value)
        if end == len(offsets):
            break
        start = max(start + 1, end - overlap)
    return chunks


def stable_point_id(collection: str, doc_id: int, chunk_index: int, text: str) -> str:
    digest = hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()[:16]
    namespace = uuid.UUID("f716c29e-49aa-45f8-9c87-a97a10c274c5")
    return str(uuid.uuid5(namespace, f"{collection}:{doc_id}:{chunk_index}:{digest}"))
