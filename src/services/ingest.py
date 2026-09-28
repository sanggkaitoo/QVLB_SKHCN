"""Pipeline nạp: extract -> AI metadata -> chunk -> embed(dense+sparse) ->
Qdrant + Postgres. GIỮ bản gốc (copy sang STORE_DIR).
Giữ các phụ lục được hỗ trợ; chống trùng theo hash và cách ly tệp lỗi.
"""
import os
import json
import uuid
import shutil
import hashlib
import re
from datetime import datetime, timezone
from collections import defaultdict
from qdrant_client import models as qm

from src.core import config, embedder, store
from src.utils import extract, metadata
from src.services.chunking import (
    build_structured_chunks, normalize_document_ref, stable_point_id,
)
from src.services.document_fields import normalize_agency, issued_day


def split_text(text: str, size: int, overlap: int,
               seps=("\nĐiều ", "\nKhoản ", "\n\n", "\n", ". ", " ")) -> list[str]:
    """Splitter đệ quy gọn (tôn trọng cấu trúc văn bản)."""
    if len(text) <= size:
        return [text] if text.strip() else []
    sep = next((s for s in seps if s in text), None)
    if sep is None:                       
        return [text[i:i + size] for i in range(0, len(text), size - overlap)]
    parts, buf, out = text.split(sep), "", []
    for p in parts:
        piece = (buf + sep + p) if buf else p
        if len(piece) <= size:
            buf = piece
        else:
            if buf:
                out.append(buf)
            buf = (out[-1][-overlap:] + sep + p) if out else p
            if len(buf) > size:           
                out.extend(split_text(buf, size, overlap, seps[1:]))
                buf = ""
    if buf.strip():
        out.append(buf)
    return [c for c in out if c.strip()]


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


class ExtractionError(RuntimeError):
    """A source file could not be converted into usable text."""


def ingest_file(file_path: str, huong: str = "di", raw_meta: dict | None = None,
                source_url: str | None = None, force: bool = False) -> int:
    raw_meta = raw_meta or {}
    name = os.path.basename(file_path)
    digest = _sha256(file_path)
    if not force:
        existing_id = store.indexed_document(digest)
        if existing_id:
            return existing_id
    print(f"\n⚙️  Đang xử lý: {name}")

    text, method = extract.extract(file_path)
    if not text:
        raise ExtractionError(
            f"Không trích xuất được nội dung từ {name}; giữ tệp để thử lại."
        )

    text = text.replace('\x00', '')
    meta = metadata.extract_metadata(text, fallback=raw_meta)
    
    # 1. Khóa cứng Số ký hiệu và Trích yếu
    meta["so_ky_hieu"] = raw_meta.get("so_ky_hieu", meta.get("so_ky_hieu"))
    meta["trich_yeu"] = raw_meta.get("trich_yeu", meta.get("trich_yeu"))
    
    # 2. Xử lý chuẩn hóa Ngày ban hành (Từ DD/MM/YYYY sang YYYY-MM-DD)
    raw_ngay = raw_meta.get("ngay_ban_hanh", "")
    if raw_ngay and "/" in raw_ngay:
        parts = raw_ngay.split("/")
        if len(parts) == 3:
            meta["ngay_ban_hanh"] = f"{parts[2].strip()}-{parts[1].strip().zfill(2)}-{parts[0].strip().zfill(2)}"
    elif raw_ngay:
        meta["ngay_ban_hanh"] = raw_ngay
    
    # 3. Khóa cứng Cơ quan ban hành (Dành cho Văn bản Đến)
    if raw_meta.get("co_quan_ban_hanh"):
        meta["co_quan_ban_hanh"] = raw_meta.get("co_quan_ban_hanh")

    archived_path = _archive_source(file_path, digest)
    with open(archived_path + ".meta.json", "w", encoding="utf-8") as stream:
        json.dump({**raw_meta, "huong": huong, "source_url": source_url}, stream, ensure_ascii=False)
    meta["normalized_so_ky_hieu"] = normalize_document_ref(meta.get("so_ky_hieu"))
    meta.update({
        "huong": huong, 
        "file_name": name, 
        "full_text": text,
        "source_url": source_url, 
        "sha256": digest,
        "extract_method": method, 
        "raw_meta": json.dumps(raw_meta, ensure_ascii=False),
        "file_path": archived_path,
    })


    chunks = build_structured_chunks(
        text, config.CHUNK_TOKENS, config.CHUNK_OVERLAP_TOKENS, doc_key=digest,
        max_parent_chars=4000, tokenizer=embedder.tokenizer(),
    )
    meta["n_chunks"] = len(chunks)

    doc_id = store.insert_document(meta)
    if doc_id == -1:
        raise RuntimeError("Không thể tạo hoặc lấy document_id cho tệp.")

    store.set_ingest_status(doc_id, "pending")
    try:
        _write_chunks(doc_id, meta, chunks, huong, name, source_url)
        store.set_ingest_status(doc_id, "ready")
    except Exception as exc:
        store.set_ingest_status(doc_id, "failed", f"{type(exc).__name__}: {exc}"[:1000])
        raise
    print(f"   Thành công: doc_id={doc_id}, {len(chunks)} đoạn Agentic")
    return doc_id


def _write_chunks(doc_id, meta, chunks, huong, name, source_url):
    def vectors():
        for offset in range(0, len(chunks), config.EMBED_BATCH_SIZE):
            batch = chunks[offset:offset + config.EMBED_BATCH_SIZE]
            texts = [f"{meta.get('so_ky_hieu') or ''}\n{chunk.section_path}\n{chunk.text}" for chunk in batch]
            encoded = embedder.encode(texts)
            if len(encoded) != len(batch):
                raise RuntimeError("Embedding count does not match chunk count")
            yield from encoded
    vecs = vectors()
    points = []
    target_collection = config.RAG_COLLECTION
    store.ensure_collection(target_collection)
    store.delete_document_chunks(doc_id, target_collection)
    for chunk, vector in zip(chunks, vecs):
        payload = {
            "text": chunk.text,
            "doc_id": doc_id,
            "so_ky_hieu": meta.get("so_ky_hieu"),
            "ngay_ban_hanh": meta.get("ngay_ban_hanh"),
            "loai_vb": meta.get("loai_vb"),
            "huong": huong,
            "file_name": name,
            "source_url": source_url,
            "co_quan_ban_hanh": meta.get("co_quan_ban_hanh"),
            "trich_yeu": meta.get("trich_yeu"),
            "chu_truong": meta.get("chu_truong", []),
            "linh_vuc": meta.get("linh_vuc", []),
            "chuyen_de": meta.get("chuyen_de", []),
            "tinh_trang_hieu_luc": meta.get("tinh_trang_hieu_luc", "chua_xac_dinh"),
            "hieu_luc_tu": meta.get("hieu_luc_tu"),
            "hieu_luc_den": meta.get("hieu_luc_den"),
            "chunk_index": chunk.chunk_index,
            "section_path": chunk.section_path,
            "parent_chunk_id": chunk.parent_chunk_id,
            "parent_text": chunk.parent_text,
            "heading": chunk.heading,
            "page_start": chunk.page_start,
            "page_end": chunk.page_end,
            "chunk_kind": "child",
            "index_version": config.INGEST_VERSION,
            "ready": False,
            "agency_normalized": normalize_agency(meta.get("co_quan_ban_hanh")),
            "issued_day": issued_day(meta.get("ngay_ban_hanh")),
        }
        points.append(qm.PointStruct(
            id=stable_point_id(target_collection, doc_id, chunk.chunk_index, chunk.text),
            vector={
                "dense": vector["dense"],
                "sparse": qm.SparseVector(
                    indices=list(vector["sparse"].keys()), values=list(vector["sparse"].values())
                ),
            },
            payload=payload,
        ))
        if len(points) >= config.EMBED_BATCH_SIZE:
            store.upsert_chunks(points, target_collection)
            points.clear()
    store.upsert_chunks(points, target_collection)
    store.publish_document_chunks(doc_id, target_collection)


def _remove_download_pair(file_path: str) -> None:
    for candidate in (file_path, file_path + ".meta.json"):
        try:
            os.remove(candidate)
        except FileNotFoundError:
            pass
        except OSError as exc:
            print(f"   ! Không dọn được {candidate}: {exc}")


def _quarantine_failed_file(file_info: dict, error: Exception) -> str:
    failed_dir = os.path.join(config.STORE_DIR, "failed_ingest")
    os.makedirs(failed_dir, exist_ok=True)

    source_path = file_info["path"]
    filename = os.path.basename(source_path)
    target_path = os.path.join(failed_dir, filename)
    if os.path.exists(target_path):
        stem, extension = os.path.splitext(filename)
        target_path = os.path.join(
            failed_dir,
            f"{stem}_{uuid.uuid4().hex[:8]}{extension}",
        )
    shutil.move(source_path, target_path)

    failure_meta = dict(file_info["meta"])
    failure_meta.update({
        "ingest_error": f"{type(error).__name__}: {error}",
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


def ingest_download_dir(download_dir: str | None = None) -> dict:
    download_dir = download_dir or config.DOWNLOAD_DIR
    result = {
        "completed_documents": [],
        "processed_files": 0,
        "filtered_files": 0,
        "failed_files": [],
    }
    store.ensure_collection()
    print("\n[AI INGEST] BẮT ĐẦU PHÂN TÍCH VÀ NẠP DỮ LIỆU...")

    groups = defaultdict(list)
    try:
        meta_files = sorted(
            name for name in os.listdir(download_dir) if name.endswith(".meta.json")
        )
    except FileNotFoundError:
        return result

    for meta_name in meta_files:
        meta_path = os.path.join(download_dir, meta_name)
        file_path = meta_path.removesuffix(".meta.json")
        if not os.path.exists(file_path):
            continue
        try:
            with open(meta_path, "r", encoding="utf-8") as stream:
                raw_meta = json.load(stream)
            file_size = os.path.getsize(file_path)
        except (OSError, json.JSONDecodeError) as exc:
            print(f"   ! Bỏ qua metadata lỗi {meta_name}: {exc}")
            continue

        document_ref = raw_meta.get("so_ky_hieu", f"unknown_{meta_name}")
        issued_date = raw_meta.get("ngay_ban_hanh", "")
        subject = raw_meta.get("trich_yeu", "")
        direction = raw_meta.get("huong", "di")
        document_key = raw_meta.get("history_key") or "|".join(
            (str(document_ref), str(issued_date), str(subject))
        )
        groups[(str(document_key), direction)].append({
            "path": file_path,
            "name": os.path.basename(file_path).lower(),
            "ext": os.path.splitext(file_path)[1].lower(),
            "size": file_size,
            "meta": raw_meta,
        })

    for (_, direction), files in groups.items():
        document_ref = files[0]["meta"].get("so_ky_hieu", "")
        print(
            f"\n[SÀNG LỌC] VB {direction.upper()}: "
            f"{document_ref or 'không số'} ({len(files)} files)"
        )
        # Keep supported attachments, including annexes that filename heuristics may miss.
        selected_paths = {item["path"] for item in files if item["ext"] in {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".csv", ".png", ".jpg", ".jpeg", ".bmp", ".tiff"}}
        group_failed = False
        for file_info in files:
            if file_info["path"] not in selected_paths:
                print(f"   [lọc] {file_info['name']}")
                result["filtered_files"] += 1
                _archive_source(file_info["path"], _sha256(file_info["path"]))
                _remove_download_pair(file_info["path"])
                continue

            try:
                ingest_file(
                    file_info["path"],
                    huong=direction,
                    raw_meta=file_info["meta"],
                    source_url=file_info["meta"].get("source_url"),
                )
            except Exception as exc:
                group_failed = True
                failure = {
                    "file": os.path.basename(file_info["path"]),
                    "error": f"{type(exc).__name__}: {exc}",
                }
                print(
                    f"   ! Bỏ qua tệp lỗi {failure['file']}; "
                    "crawler tiếp tục với tệp kế tiếp."
                )
                try:
                    failure["quarantined_file"] = _quarantine_failed_file(file_info, exc)
                    print(f"   ! Đã chuyển tệp lỗi tới {failure['quarantined_file']}")
                except Exception as quarantine_error:
                    failure["quarantine_error"] = (
                        f"{type(quarantine_error).__name__}: {quarantine_error}"
                    )
                    print(f"   ! Không thể cách ly tệp lỗi: {quarantine_error}")
                result["failed_files"].append(failure)
                continue

            result["processed_files"] += 1
            _remove_download_pair(file_info["path"])

        if not group_failed:
            result["completed_documents"].append(files[0]["meta"])

    print(
        "\n[AI INGEST] HOÀN TẤT: "
        f"{result['processed_files']} tệp thành công, "
        f"{len(result['failed_files'])} tệp lỗi, "
        f"{result['filtered_files']} tệp được lọc."
    )
    return result


if __name__ == "__main__":
    ingest_download_dir()
