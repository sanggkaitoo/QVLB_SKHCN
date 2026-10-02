"""Pipeline nạp theo VĂN BẢN: một văn bản gồm nhiều tệp (bản chính, bản sao, đính kèm).

extract từng tệp -> AI metadata (một lần, từ tệp chính) -> phát hiện tệp trùng nội dung
-> chunk -> embed (dense+sparse, kèm ngữ cảnh văn bản) -> Qdrant + Postgres.
Chỉ giữ bản gốc (copy sang STORE_DIR) và cách ly tệp lỗi khi bật "lưu tệp gốc" trên trang quản trị
(mặc định tắt: bản gốc xem trên QLVB); chống nạp trùng theo SHA-256.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone

from qdrant_client import models as qm

from src.core import config, embedder, store
from src.services.chunking import build_structured_chunks, stable_point_id
from src.services.document_fields import (
    FILE_ROLE_LABELS, clean_placeholder, issued_day, normalize_agency, normalize_document_ref, reconcile_reference,
)
from src.services import source_store
from src.services.relations import extract_relations
from src.utils import extract, metadata
from src.utils.extract import ExtractionError

logger = logging.getLogger(__name__)

__all__ = ["ExtractionError", "SourceFile", "ingest_document", "ingest_file", "ingest_download_dir"]

# Chất lượng văn bản trích xuất: số nhỏ hơn được ưu tiên làm bản đại diện khi hai tệp trùng nội dung.
_METHOD_RANK = {"pdf_text": 0, "docx": 0, "doc_libre": 1, "xlsx": 1, "xls": 1, "csv": 1,
                "pdf_mixed_ocr": 2, "ocr_tesseract": 3}
_FILE_INDEX_RE = re.compile(r"_(\d{2})_")


@dataclass
class SourceFile:
    path: str
    file_index: int = 0
    text: str | None = None          # văn bản đã trích (reindex tái sử dụng)
    method: str | None = None
    name: str | None = None          # reindex khi không còn tệp gốc: tên và SHA-256 lấy từ database
    digest: str | None = None


@dataclass
class _PreparedFile:
    source: SourceFile
    name: str
    digest: str
    archived_path: str
    text: str = ""
    method: str | None = None
    error: str | None = None
    warning: str | None = None           # tệp đọc được nhưng đã bị cắt bớt vì quá lớn
    role: str = "dinh_kem"
    duplicate_of: int | None = None      # index trong danh sách prepared
    file_id: int | None = None
    n_chunks: int = 0
    signature: set = field(default_factory=set)


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for blk in iter(lambda: f.read(8192), b""):
            h.update(blk)
    return h.hexdigest()


def _archive_source(file_path: str, digest: str) -> str:
    target_dir = os.path.join(config.STORE_DIR, digest[:2], digest)
    os.makedirs(target_dir, exist_ok=True)
    target = os.path.join(target_dir, os.path.basename(file_path))
    if os.path.abspath(file_path) != os.path.abspath(target) and not os.path.exists(target):
        shutil.copy2(file_path, target)
    return target


def file_index_from_name(name: str, default: int = 0) -> int:
    match = _FILE_INDEX_RE.search(name or "")
    return int(match.group(1)) if match else default


def source_key_for(raw_meta: dict, huong: str | None, digest: str) -> str:
    """Khóa định danh văn bản: khóa crawler (số ký hiệu + ngày) theo hướng, nếu không thì theo tệp."""
    history_key = str(raw_meta.get("history_key") or "").strip()
    if history_key:
        return f"{huong or raw_meta.get('huong') or 'di'}|{history_key}"
    return f"sha256:{digest}"


def _signature(text: str) -> set:
    words = re.findall(r"\w+", text.lower())
    if len(words) < 25:
        return {" ".join(words)}
    return {hash(" ".join(words[i:i + 3])) for i in range(len(words) - 2)}


def _similarity(first: set, second: set) -> float:
    """Containment of word 3-grams, robust to OCR noise and format differences between copies.

    Files of very different length are never considered copies (an excerpt is not the document).
    """
    if not first or not second:
        return 0.0
    small, large = sorted((len(first), len(second)))
    if small / large < 0.6:
        return 0.0
    return len(first & second) / small


def assign_roles(prepared: list[_PreparedFile], threshold: float | None = None) -> None:
    """Cluster near-identical files; one representative per cluster is indexed.

    The first readable file is the main văn bản; files with the same content become 'ban_sao'.
    """
    threshold = config.DUPLICATE_FILE_SIMILARITY if threshold is None else threshold
    readable = [index for index, item in enumerate(prepared) if item.text]
    for index in readable:
        prepared[index].signature = _signature(prepared[index].text)
    representatives: list[int] = []
    cluster_of: dict[int, int] = {}
    for index in readable:
        match = next((rep for rep in representatives
                      if _similarity(prepared[index].signature, prepared[rep].signature) >= threshold), None)
        if match is None:
            representatives.append(index)
            cluster_of[index] = index
            continue
        cluster_of[index] = match
        rank = _METHOD_RANK.get(prepared[index].method or "", 5)
        if rank < _METHOD_RANK.get(prepared[match].method or "", 5):
            # A cleaner extraction of the same content represents the cluster instead.
            representatives[representatives.index(match)] = index
            for member, rep in list(cluster_of.items()):
                if rep == match:
                    cluster_of[member] = index
    main_cluster = cluster_of[readable[0]] if readable else None
    for index in readable:
        rep = cluster_of[index]
        if index != rep:
            prepared[index].role = "ban_sao"
            prepared[index].duplicate_of = rep
        else:
            prepared[index].role = "chinh" if rep == main_cluster else "dinh_kem"


def _truncate(text: str, limit: int, what: str) -> tuple[str, str | None]:
    """Cắt văn bản quá dài (kèm ghi chú trong nội dung) thay vì để cả lần nạp thất bại."""
    if len(text) <= limit:
        return text, None
    cut = text.rfind("\n", 0, limit)
    cut = cut if cut > limit * 0.9 else limit
    note = (f"{what} có {len(text):,} ký tự, vượt giới hạn {limit:,}; chỉ nạp phần đầu "
            f"({cut:,} ký tự)").replace(",", ".")
    return text[:cut] + f"\n\n[[ĐÃ CẮT BỚT: {note}. Xem tệp gốc để có đầy đủ nội dung.]]", note


def _stored_path(source: SourceFile, digest: str, keep: bool) -> str:
    """Đường dẫn bản gốc ghi vào database; rỗng khi không lưu tệp gốc."""
    on_disk = bool(source.path) and os.path.isfile(source.path)
    if keep and on_disk:
        return _archive_source(source.path, digest)
    return source.path if on_disk and source_store.is_stored(source.path) else ""


def _prepare(files: list[SourceFile]) -> list[_PreparedFile]:
    prepared = []
    keep = source_store.keep_enabled()
    for source in sorted(files, key=lambda item: (item.file_index, item.name or os.path.basename(item.path))):
        name = source.name or os.path.basename(source.path)
        on_disk = bool(source.path) and os.path.isfile(source.path)
        digest = source.digest or _sha256(source.path)
        item = _PreparedFile(source=source, name=name, digest=digest,
                             archived_path=_stored_path(source, digest, keep))
        if source.text:
            item.text, item.method = source.text.replace("\x00", ""), source.method
        elif not on_disk:
            item.error = "Không còn tệp gốc để trích xuất (xem bản gốc trên QLVB)"
        else:
            try:
                text, method = extract.extract(source.path)
                item.text, item.method = (text or "").replace("\x00", "").strip(), method
                if not item.text:
                    item.error = "Tệp không có nội dung văn bản trích xuất được"
            except ExtractionError as exc:
                item.error = str(exc)
        if item.text:
            item.text, item.warning = _truncate(item.text, config.EXTRACT_MAX_FILE_CHARS, f"Tệp {name}")
            if item.warning:
                logger.warning("%s", item.warning)
        prepared.append(item)
    return prepared


def _write_sidecar(item: _PreparedFile, raw_meta: dict, huong: str | None, source_url: str | None) -> None:
    """Metadata QLVB cạnh bản gốc lưu trữ, để có thể nạp lại độc lập với database."""
    if not item.archived_path:  # không lưu tệp gốc
        return
    sidecar = item.archived_path + ".meta.json"
    if os.path.exists(sidecar) or not raw_meta:
        return
    try:
        with open(sidecar, "w", encoding="utf-8") as stream:
            json.dump({**raw_meta, "huong": huong, "source_url": source_url, "file_index": item.source.file_index},
                      stream, ensure_ascii=False)
    except OSError as exc:
        logger.warning("Không ghi được metadata kèm tệp %s: %s", item.name, exc)


def _document_metadata(main_text: str, raw_meta: dict, reuse_metadata: dict | None) -> dict:
    if reuse_metadata:
        meta = {key: value for key, value in reuse_metadata.items() if value is not None}
        meta["ngay_ban_hanh"] = metadata.normalize_date(meta.get("ngay_ban_hanh"))
    else:
        meta = metadata.extract_metadata(main_text, fallback=raw_meta)
    # Metadata từ QLVB là nguồn chính thức, ưu tiên hơn giá trị do AI trích (sau khi bỏ giá trị giữ chỗ).
    for key in ("trich_yeu", "co_quan_ban_hanh"):
        value = clean_placeholder(raw_meta.get(key))
        if value:
            meta[key] = value
    meta["so_ky_hieu"] = reconcile_reference(raw_meta.get("so_ky_hieu") or meta.get("so_ky_hieu"),
                                             meta.get("so_ky_hieu"), main_text)
    # Crawler API cung cấp loại văn bản và người ký chính thức từ QLVB.
    official_type = metadata.loai_from_label(raw_meta.get("loai_van_ban"))
    if official_type:
        meta["loai_vb"], meta["viet_tat_loai"] = official_type, metadata.LOAI_VB.get(official_type, "")
    if clean_placeholder(raw_meta.get("nguoi_ky")):
        meta["nguoi_ky"] = clean_placeholder(raw_meta.get("nguoi_ky"))
    if raw_meta.get("ngay_ban_hanh"):
        meta["ngay_ban_hanh"] = metadata.normalize_date(raw_meta["ngay_ban_hanh"]) or meta.get("ngay_ban_hanh")
    meta["normalized_so_ky_hieu"] = normalize_document_ref(meta.get("so_ky_hieu")) or None
    return meta


def infer_direction(huong: str | None, so_ky_hieu: str | None) -> str | None:
    if huong in {"di", "den"}:
        return huong
    code = config.OWN_AGENCY_CODE
    if code and re.search(rf"(^|[/\-]){re.escape(code)}($|[-/])", normalize_document_ref(so_ky_hieu)):
        return "di"
    return None


def _summary_text(meta: dict, prepared: list[_PreparedFile]) -> str:
    loai = str(meta.get("loai_vb") or "").replace("_", " ")
    parts = [
        f"Số ký hiệu: {meta.get('so_ky_hieu') or 'không rõ'}.",
        f"Loại văn bản: {loai or 'không rõ'}.",
        f"Ngày ban hành: {meta.get('ngay_ban_hanh') or 'không rõ'}.",
        f"Cơ quan ban hành: {meta.get('co_quan_ban_hanh') or 'không rõ'}.",
    ]
    if meta.get("nguoi_ky"):
        parts.append(f"Người ký: {meta['nguoi_ky']} {meta.get('chuc_vu_nguoi_ky') or ''}".strip() + ".")
    parts.append(f"Trích yếu: {meta.get('trich_yeu') or 'không có trích yếu'}.")
    if meta.get("chu_truong"):
        parts.append("Chủ trương: " + "; ".join(meta["chu_truong"]) + ".")
    if meta.get("chuyen_de"):
        parts.append("Chuyên đề: " + "; ".join(meta["chuyen_de"]) + ".")
    attachments = [item.name for item in prepared if item.role == "dinh_kem" and item.text]
    if attachments:
        parts.append("Tệp đính kèm: " + "; ".join(attachments[:10]) + ".")
    return " ".join(parts)


def _embedding_header(meta: dict, item: _PreparedFile) -> str:
    header = " | ".join(value for value in (
        meta.get("so_ky_hieu"),
        str(meta.get("loai_vb") or "").replace("_", " "),
        str(meta.get("trich_yeu") or "")[:180],
    ) if value)
    if item.role != "chinh":
        header += f" | {FILE_ROLE_LABELS[item.role]}: {item.name}"
    return header


def _base_payload(doc_id: int, meta: dict, huong: str | None, source_url: str | None, run_id: str) -> dict:
    return {
        "doc_id": doc_id,
        "so_ky_hieu": meta.get("so_ky_hieu"),
        "ngay_ban_hanh": meta.get("ngay_ban_hanh"),
        "loai_vb": meta.get("loai_vb"),
        "huong": huong,
        "source_url": source_url,
        "co_quan_ban_hanh": meta.get("co_quan_ban_hanh"),
        "trich_yeu": meta.get("trich_yeu"),
        "chu_truong": meta.get("chu_truong") or [],
        "linh_vuc": meta.get("linh_vuc") or [],
        "chuyen_de": meta.get("chuyen_de") or [],
        "tinh_trang_hieu_luc": meta.get("tinh_trang_hieu_luc") or "chua_xac_dinh",
        "hieu_luc_tu": str(meta.get("hieu_luc_tu") or "") or None,
        "hieu_luc_den": str(meta.get("hieu_luc_den") or "") or None,
        "index_version": config.INGEST_VERSION,
        "ingest_run": run_id,
        "ready": False,
        "agency_normalized": normalize_agency(meta.get("co_quan_ban_hanh")),
        "issued_day": issued_day(meta.get("ngay_ban_hanh")),
    }


def _point(point_id: str, vector: dict, payload: dict) -> qm.PointStruct:
    return qm.PointStruct(
        id=point_id,
        vector={
            "dense": vector["dense"],
            "sparse": qm.SparseVector(indices=list(vector["sparse"].keys()), values=list(vector["sparse"].values())),
        },
        payload=payload,
    )


def _write_points(doc_id: int, meta: dict, prepared: list[_PreparedFile], huong, source_url) -> int:
    collection = config.RAG_COLLECTION
    store.ensure_collection(collection)
    run_id = uuid.uuid4().hex
    base = _base_payload(doc_id, meta, huong, source_url, run_id)
    main = next(item for item in prepared if item.role == "chinh")

    summary = _summary_text(meta, prepared)
    summary_payload = {**base, "text": summary, "file_id": main.file_id, "file_name": main.name,
                       "file_role": "chinh", "chunk_index": -1, "chunk_kind": "document_summary",
                       "section_path": "Thông tin văn bản"}
    store.upsert_chunks([_point(stable_point_id(collection, f"{doc_id}-summary", -1, summary),
                                embedder.encode([summary])[0], summary_payload)], collection)
    total = 0
    tokenizer = embedder.tokenizer()
    for item in prepared:
        if not item.text or item.role == "ban_sao":
            continue
        chunks = build_structured_chunks(item.text, config.CHUNK_TOKENS, config.CHUNK_OVERLAP_TOKENS,
                                         doc_key=item.digest, max_parent_chars=4000, tokenizer=tokenizer)
        header = _embedding_header(meta, item)
        points = []
        for offset in range(0, len(chunks), config.EMBED_BATCH_SIZE):
            batch = chunks[offset:offset + config.EMBED_BATCH_SIZE]
            vectors = embedder.encode([f"{header}\n{chunk.section_path}\n{chunk.text}" for chunk in batch])
            if len(vectors) != len(batch):
                raise RuntimeError("Embedding count does not match chunk count")
            for chunk, vector in zip(batch, vectors):
                payload = {
                    **base, "text": chunk.text, "file_id": item.file_id, "file_name": item.name,
                    "file_role": item.role, "file_index": item.source.file_index,
                    "chunk_index": chunk.chunk_index, "section_path": chunk.section_path,
                    "parent_chunk_id": chunk.parent_chunk_id, "parent_text": chunk.parent_text,
                    "heading": chunk.heading, "page_start": chunk.page_start, "page_end": chunk.page_end,
                    "chunk_kind": "child",
                }
                points.append(_point(stable_point_id(collection, f"{doc_id}-{item.file_id}", chunk.chunk_index, chunk.text),
                                     vector, payload))
            store.upsert_chunks(points, collection)
            points.clear()
        item.n_chunks = len(chunks)
        total += len(chunks)
    # Only after every point of this run exists: drop older runs, then publish atomically per văn bản.
    store.delete_stale_document_chunks(doc_id, run_id, collection)
    store.publish_document_chunks(doc_id, collection)
    return total


def ingest_document(files: list[SourceFile], huong: str | None = "di", raw_meta: dict | None = None,
                    source_url: str | None = None, force: bool = False,
                    reuse_metadata: dict | None = None, source_key: str | None = None) -> dict:
    """Nạp một văn bản gồm nhiều tệp. Trả {'document_id', 'skipped', 'failed_files': [(path, error)]}.

    Tệp không trích xuất được được ghi nhận (trạng thái failed) nhưng không chặn các tệp còn lại.
    Ném ExtractionError khi không tệp nào đọc được.
    """
    if not files:
        raise ValueError("Văn bản phải có ít nhất một tệp")
    raw_meta = dict(raw_meta or {})
    prepared = _prepare(files)
    readable = [item for item in prepared if item.text]
    failed = [(item.source.path, item.error) for item in prepared if item.error]
    key = source_key or source_key_for(raw_meta, huong, (readable or prepared)[0].digest)

    existing = store.document_by_source_key(key)
    if (not force and existing and existing["ingest_status"] == "ready"
            and existing["index_version"] == config.INGEST_VERSION
            and {item.digest for item in readable} <= store.ready_file_hashes(existing["id"])):
        return {"document_id": existing["id"], "skipped": True, "failed_files": failed}
    if not readable:
        raise ExtractionError("; ".join(error for _, error in failed if error) or "Không có tệp đọc được")

    assign_roles(prepared)
    main = next(item for item in prepared if item.role == "chinh")
    for item in prepared:
        _write_sidecar(item, raw_meta, huong, source_url)
    logger.info("Nạp văn bản %s: %d tệp (%d đọc được)", raw_meta.get("so_ky_hieu") or main.name,
                len(prepared), len(readable))
    meta = _document_metadata(main.text, raw_meta, reuse_metadata)
    huong = infer_direction(huong, meta.get("so_ky_hieu"))

    indexed = [item for item in prepared if item.text and item.role != "ban_sao"]
    full_text = main.text + "".join(f"\n\n[[TỆP ĐÍNH KÈM: {item.name}]]\n{item.text}"
                                    for item in indexed if item is not main)
    full_text, document_warning = _truncate(full_text, config.EXTRACT_MAX_DOCUMENT_CHARS, "Nội dung ghép các tệp")
    warnings = [item.warning for item in prepared if item.warning] + ([document_warning] if document_warning else [])
    if warnings:
        raw_meta["extract_warnings"] = warnings
    document_fields = {
        **{column: meta.get(column) for column in (
            "so_ky_hieu", "normalized_so_ky_hieu", "ngay_ban_hanh", "loai_vb", "viet_tat_loai",
            "co_quan_ban_hanh", "nguoi_ky", "chuc_vu_nguoi_ky", "trich_yeu", "chu_truong", "linh_vuc",
            "chuyen_de", "vai_tro_van_ban", "extract_confidence")},
        "huong": huong, "source_url": source_url, "raw_meta": raw_meta, "full_text": full_text,
        "file_name": main.name, "file_path": main.archived_path, "sha256": main.digest,
        "extract_method": main.method, "n_files": len(prepared),
    }
    doc_id = store.upsert_document(key, document_fields)
    store.set_ingest_status(doc_id, "pending")

    for item in prepared:
        item.file_id = store.upsert_file(doc_id, item.digest, {
            "file_index": item.source.file_index, "file_name": item.name, "file_path": item.archived_path,
            "role": item.role, "duplicate_of": None, "extract_method": item.method,
            "full_text": item.text or None, "n_chunks": 0,
            "ingest_status": "failed" if item.error else "pending",
            "index_version": config.INGEST_VERSION, "ingest_error": item.error,
        })
    store.delete_files_except(doc_id, [item.file_id for item in prepared])
    for item in prepared:
        if item.duplicate_of is not None:
            store.update_file(item.file_id, duplicate_of=prepared[item.duplicate_of].file_id)

    try:
        total_chunks = _write_points(doc_id, meta, prepared, huong, source_url)
        relations = extract_relations("\n".join(item.text for item in indexed), own_ref=meta.get("so_ky_hieu"))
        for relation in relations:
            relation["target_document_id"] = store.resolve_reference(relation["normalized_target_ref"])
        store.replace_document_relations(doc_id, relations)
        for item in prepared:
            if item.error:
                continue
            store.update_file(item.file_id, n_chunks=item.n_chunks,
                              ingest_status="duplicate" if item.role == "ban_sao" else "ready")
        store.upsert_document(key, {"n_chunks": total_chunks})
        store.set_ingest_status(doc_id, "ready")
    except Exception as exc:
        store.set_ingest_status(doc_id, "failed", f"{type(exc).__name__}: {exc}"[:1000])
        raise
    logger.info("Đã nạp doc_id=%s: %d đoạn, %d tệp trùng nội dung, %d quan hệ", doc_id, total_chunks,
                sum(item.role == "ban_sao" for item in prepared), len(relations))
    return {"document_id": doc_id, "skipped": False, "failed_files": failed, "warnings": warnings}


def ingest_file(file_path: str, huong: str | None = "di", raw_meta: dict | None = None,
                source_url: str | None = None, force: bool = False) -> int:
    """Nạp một tệp đơn lẻ như một văn bản (tải lên thủ công, mẫu đánh giá)."""
    raw_meta = raw_meta or {}
    index = int(raw_meta.get("file_index") or file_index_from_name(os.path.basename(file_path)))
    source = SourceFile(file_path, index)
    result = ingest_document([source], huong=huong, raw_meta=raw_meta, source_url=source_url, force=force)
    return result["document_id"]


def _remove_download_pair(file_path: str) -> None:
    for candidate in (file_path, file_path + ".meta.json"):
        try:
            os.remove(candidate)
        except FileNotFoundError:
            pass
        except OSError as exc:
            logger.warning("Không dọn được %s: %s", candidate, exc)


def _quarantine_failed_file(file_info: dict, error) -> str:
    failed_dir = os.path.join(config.STORE_DIR, "failed_ingest")
    os.makedirs(failed_dir, exist_ok=True)

    source_path = file_info["path"]
    filename = os.path.basename(source_path)
    target_path = os.path.join(failed_dir, filename)
    if os.path.exists(target_path):
        stem, extension = os.path.splitext(filename)
        target_path = os.path.join(failed_dir, f"{stem}_{uuid.uuid4().hex[:8]}{extension}")
    shutil.move(source_path, target_path)

    failure_meta = dict(file_info["meta"])
    failure_meta.update({
        "ingest_error": str(error),
        "failed_at": datetime.now(timezone.utc).isoformat(),
        "quarantined_file": target_path,
    })
    with open(target_path + ".meta.json", "w", encoding="utf-8") as stream:
        json.dump(failure_meta, stream, ensure_ascii=False, indent=2)

    try:
        os.remove(source_path + ".meta.json")
    except FileNotFoundError:
        pass
    return target_path


def _record_failure(result: dict, file_info: dict, error) -> None:
    failure = {"file": os.path.basename(file_info["path"]), "error": str(error)[:500]}
    if not source_store.keep_enabled():  # không lưu tệp gốc: tệp lỗi xem lại trên QLVB
        _remove_download_pair(file_info["path"])
    else:
        try:
            failure["quarantined_file"] = _quarantine_failed_file(file_info, error)
        except Exception as quarantine_error:
            failure["quarantine_error"] = f"{type(quarantine_error).__name__}: {quarantine_error}"
    logger.warning("Bỏ qua tệp lỗi %s: %s", failure["file"], failure["error"])
    result["failed_files"].append(failure)


def ingest_download_dir(download_dir: str | None = None) -> dict:
    download_dir = download_dir or config.DOWNLOAD_DIR
    result = {
        "completed_documents": [],
        "processed_files": 0,
        "filtered_files": 0,
        "failed_files": [],
    }
    try:
        meta_files = sorted(name for name in os.listdir(download_dir) if name.endswith(".meta.json"))
    except FileNotFoundError:
        return result
    store.ensure_collection()
    logger.info("Bắt đầu nạp thư mục tải về (%d tệp metadata)", len(meta_files))

    groups = defaultdict(list)
    for meta_name in meta_files:
        meta_path = os.path.join(download_dir, meta_name)
        file_path = meta_path.removesuffix(".meta.json")
        if not os.path.exists(file_path):
            continue
        try:
            with open(meta_path, "r", encoding="utf-8") as stream:
                raw_meta = json.load(stream)
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning("Bỏ qua metadata lỗi %s: %s", meta_name, exc)
            continue
        document_key = raw_meta.get("history_key") or "|".join(
            str(raw_meta.get(key, "")) for key in ("so_ky_hieu", "ngay_ban_hanh", "trich_yeu"))
        direction = raw_meta.get("huong", "di")
        name = os.path.basename(file_path)
        groups[(str(document_key), direction)].append({
            "path": file_path,
            "ext": os.path.splitext(file_path)[1].lower().lstrip("."),
            "index": int(raw_meta.get("file_index") or file_index_from_name(name)),
            "meta": raw_meta,
        })

    for (_, direction), files in groups.items():
        files.sort(key=lambda item: (item["index"], item["path"]))
        supported = []
        for file_info in files:
            if file_info["ext"] in extract.SUPPORTED_EXTENSIONS:
                supported.append(file_info)
                continue
            logger.info("Lọc tệp không hỗ trợ %s", os.path.basename(file_info["path"]))
            result["filtered_files"] += 1
            if source_store.keep_enabled():
                _archive_source(file_info["path"], _sha256(file_info["path"]))
            _remove_download_pair(file_info["path"])
        if not supported:
            result["completed_documents"].append(files[0]["meta"])
            continue

        raw_meta = supported[0]["meta"]
        by_path = {item["path"]: item for item in supported}
        try:
            outcome = ingest_document(
                [SourceFile(item["path"], item["index"]) for item in supported],
                huong=direction, raw_meta=raw_meta, source_url=raw_meta.get("source_url"),
            )
        except Exception as exc:
            for file_info in supported:
                _record_failure(result, file_info, f"{type(exc).__name__}: {exc}")
            continue

        failed_paths = {path for path, _ in outcome["failed_files"]}
        for path, error in outcome["failed_files"]:
            _record_failure(result, by_path[path], error)
        for file_info in supported:
            if file_info["path"] in failed_paths:
                continue
            result["processed_files"] += 1
            _remove_download_pair(file_info["path"])
        if not failed_paths:
            result["completed_documents"].append(raw_meta)

    logger.info("Hoàn tất nạp: %d tệp thành công, %d tệp lỗi, %d tệp được lọc",
                result["processed_files"], len(result["failed_files"]), result["filtered_files"])
    return result


if __name__ == "__main__":
    ingest_download_dir()
