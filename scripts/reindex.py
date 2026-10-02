"""Tạo lại index agentic-v4 cho các văn bản đã đăng ký (sau migration 004 hoặc khi đổi cách chunk/embed).

Mặc định tái sử dụng văn bản đã trích xuất và metadata đã có (không gọi LLM, không OCR lại).
Dùng --re-extract để trích xuất lại từ tệp gốc, --refresh-metadata để AI trích metadata lại.
"""
import argparse
import logging
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import psycopg2.extras  # noqa: E402

from src.core import config, store  # noqa: E402
from src.services.ingest import SourceFile, ingest_document  # noqa: E402

_METADATA_FIELDS = ("so_ky_hieu", "ngay_ban_hanh", "loai_vb", "viet_tat_loai", "co_quan_ban_hanh", "nguoi_ky",
                    "chuc_vu_nguoi_ky", "trich_yeu", "chu_truong", "linh_vuc", "chuyen_de", "vai_tro_van_ban",
                    "extract_confidence", "tinh_trang_hieu_luc", "hieu_luc_tu", "hieu_luc_den")


def _documents(args):
    conditions, params = ["d.id > %s"], [args.start_id]
    if args.document_id:
        conditions.append("d.id = %s")
        params.append(args.document_id)
    if args.ids:
        conditions.append("d.id = ANY(%s)")
        params.append([int(value) for value in args.ids.split(",") if value.strip()])
    if not args.all:
        conditions.append("NOT (d.ingest_status = 'ready' AND d.index_version = %s)")
        params.append(config.INGEST_VERSION)
    with store.pg() as connection, connection.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cursor:
        cursor.execute(f"SELECT * FROM documents d WHERE {' AND '.join(conditions)} ORDER BY d.id", params)
        rows = [dict(row) for row in cursor.fetchall()]
    return rows[: args.limit] if args.limit else rows


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-id", type=int, default=0)
    parser.add_argument("--document-id", type=int)
    parser.add_argument("--ids", help="Danh sách id văn bản, cách nhau bằng dấu phẩy")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--all", action="store_true", help="Index lại cả văn bản đã sẵn sàng ở phiên bản hiện tại")
    parser.add_argument("--re-extract", action="store_true", help="Trích xuất lại văn bản từ tệp gốc")
    parser.add_argument("--refresh-metadata", action="store_true", help="Gọi AI trích metadata lại")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")

    store.ensure_collection()
    documents = _documents(args)
    print(f"Collection: {config.RAG_COLLECTION}; index version: {config.INGEST_VERSION}; văn bản cần xử lý: {len(documents)}")
    processed = failed = 0
    for document in documents:
        files = store.document_files(document["id"], include_text=True)
        sources, missing = [], []
        for item in files:
            on_disk = bool(item["file_path"]) and os.path.isfile(item["file_path"])
            has_text = bool(item["full_text"]) and item["ingest_status"] != "failed"
            # Không còn tệp gốc (mặc định không lưu): dùng văn bản đã trích trong database, kể cả khi --re-extract.
            reuse = has_text and (not args.re_extract or not on_disk)
            if not on_disk:
                missing.append(item["file_name"])
            sources.append(SourceFile(item["file_path"] if on_disk else "", item["file_index"],
                                      text=item["full_text"] if reuse else None,
                                      method=item["extract_method"] if reuse else None,
                                      name=item["file_name"], digest=item["sha256"]))
        label = f"doc_id={document['id']} {document.get('so_ky_hieu') or ''} ({len(sources)} tệp)"
        if missing and args.re_extract:
            print(f"[warn] {label}: không còn tệp gốc, giữ văn bản đã trích cho {missing}")
        if args.dry_run or not sources:
            print(("[dry-run] " if args.dry_run else "[skip] văn bản không có tệp ") + label)
            continue
        reuse_metadata = None if args.refresh_metadata else {key: document.get(key) for key in _METADATA_FIELDS}
        try:
            outcome = ingest_document(sources, huong=document.get("huong"), raw_meta=document.get("raw_meta") or {},
                                      source_url=document.get("source_url"), force=True,
                                      reuse_metadata=reuse_metadata, source_key=document.get("source_key"))
            processed += 1
            print(f"[ok] {label}" + (f"; tệp lỗi: {len(outcome['failed_files'])}" if outcome["failed_files"] else ""))
        except Exception as exc:
            failed += 1
            print(f"[failed] {label}: {type(exc).__name__}: {exc}")
        print(f"[checkpoint] next --start-id {document['id']}")
    print(f"Xong: {processed} thành công, {failed} lỗi.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
