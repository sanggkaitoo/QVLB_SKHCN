from __future__ import annotations

import argparse
import sys
from pathlib import Path

from qdrant_client import models as qm

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core import config, embedder, store
from src.services.chunking import build_structured_chunks, stable_point_id


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Re-index Postgres sang collection Agentic RAG v2.")
    parser.add_argument(
        "--collection",
        default=(config.RAG_COLLECTION if config.RAG_COLLECTION != config.QDRANT_COLLECTION else config.QDRANT_COLLECTION + "_v2"),
    )
    parser.add_argument("--start-id", type=int, default=0)
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--batch-size", type=int, default=20)
    return parser.parse_args()


def fetch_documents(after_id: int, limit: int):
    with store.pg() as connection, connection.cursor() as cursor:
        cursor.execute(
            """SELECT id, so_ky_hieu, ngay_ban_hanh, loai_vb, huong, file_name,
                      co_quan_ban_hanh, trich_yeu, chu_truong, linh_vuc, chuyen_de,
                      full_text, source_url, sha256, tinh_trang_hieu_luc, hieu_luc_tu, hieu_luc_den
               FROM documents
               WHERE id > %s AND full_text IS NOT NULL AND btrim(full_text) <> ''
               ORDER BY id LIMIT %s""",
            (after_id, limit),
        )
        columns = [description[0] for description in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]


def points_for_document(document: dict, collection: str):
    chunks = build_structured_chunks(
        document["full_text"], config.CHUNK_SIZE, config.CHUNK_OVERLAP,
        doc_key=str(document.get("sha256") or document["id"]),
    )
    vectors = embedder.encode([chunk.text for chunk in chunks])
    points = []
    for chunk, vector in zip(chunks, vectors):
        payload = {
            "text": chunk.text,
            "doc_id": document["id"],
            "so_ky_hieu": document.get("so_ky_hieu"),
            "ngay_ban_hanh": str(document.get("ngay_ban_hanh") or ""),
            "loai_vb": document.get("loai_vb"),
            "huong": document.get("huong"),
            "file_name": document.get("file_name"),
            "co_quan_ban_hanh": document.get("co_quan_ban_hanh"),
            "trich_yeu": document.get("trich_yeu"),
            "chu_truong": document.get("chu_truong") or [],
            "linh_vuc": document.get("linh_vuc") or [],
            "chuyen_de": document.get("chuyen_de") or [],
            "source_url": document.get("source_url"),
            "tinh_trang_hieu_luc": document.get("tinh_trang_hieu_luc"),
            "hieu_luc_tu": str(document.get("hieu_luc_tu") or ""),
            "hieu_luc_den": str(document.get("hieu_luc_den") or ""),
            "chunk_index": chunk.chunk_index,
            "section_path": chunk.section_path,
            "parent_chunk_id": chunk.parent_chunk_id,
            "parent_text": chunk.parent_text,
            "heading": chunk.heading,
            "page_start": chunk.page_start,
            "page_end": chunk.page_end,
            "chunk_kind": "child",
        }
        points.append(qm.PointStruct(
            id=stable_point_id(collection, int(document["id"]), chunk.chunk_index, chunk.text),
            vector={
                "dense": vector["dense"],
                "sparse": qm.SparseVector(
                    indices=list(vector["sparse"].keys()), values=list(vector["sparse"].values())
                ),
            },
            payload=payload,
        ))
    return points


def main() -> int:
    args = parse_args()
    if args.collection == config.QDRANT_COLLECTION:
        raise SystemExit("Collection đích phải khác QDRANT_COLLECTION để không ghi đè dữ liệu cũ.")
    store.ensure_collection(args.collection)
    processed = 0
    after_id = args.start_id
    while args.limit is None or processed < args.limit:
        remaining = args.batch_size if args.limit is None else min(args.batch_size, args.limit - processed)
        documents = fetch_documents(after_id, remaining)
        if not documents:
            break
        for document in documents:
            points = points_for_document(document, args.collection)
            store.upsert_chunks(points, args.collection)
            processed += 1
            after_id = int(document["id"])
            print(f"[reindex] doc_id={after_id} chunks={len(points)} total_docs={processed}", flush=True)
    print(f"Hoàn tất {processed} văn bản vào collection {args.collection}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
