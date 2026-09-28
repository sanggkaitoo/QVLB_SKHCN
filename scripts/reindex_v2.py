"""Compatibility entry point: re-extract original files into the active v3 index."""
import argparse
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.core import config, store
from src.services.ingest import ingest_file


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--collection", default=config.RAG_COLLECTION)
    parser.add_argument("--start-id", type=int, default=0)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--batch-size", type=int, default=20)
    args = parser.parse_args()
    if args.collection != config.RAG_COLLECTION:
        parser.error("Use the configured RAG_COLLECTION for v3 ingestion")
    if args.batch_size < 1 or (args.limit is not None and args.limit < 1):
        parser.error("Batch size and limit must be positive")
    after, processed, failed = args.start_id, 0, 0
    while args.limit is None or processed < args.limit:
        limit = min(args.batch_size, args.limit - processed) if args.limit else args.batch_size
        with store.pg() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT id, file_path, huong, raw_meta, source_url FROM documents WHERE id > %s ORDER BY id LIMIT %s", (after, limit))
            rows = cursor.fetchall()
        if not rows:
            break
        for doc_id, path, direction, metadata, url in rows:
            try:
                ingest_file(path, huong=direction, raw_meta=metadata or {}, source_url=url, force=True)
            except Exception as exc:
                failed += 1
                print(f"[failed] doc_id={doc_id}: {type(exc).__name__}: {exc}")
            after = doc_id
            processed += 1
            print(f"[checkpoint] next --start-id {after}")
    print(f"Processed: {processed}; failed: {failed}. Empty DB must be populated by crawl/import first.")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
