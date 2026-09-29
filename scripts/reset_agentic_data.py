"""Explicit local-project reset. Never called by run.sh."""
from pathlib import Path
import argparse
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import psycopg2
from psycopg2.extensions import parse_dsn
from src.core import config, store


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--confirm-delete-qlvb", action="store_true")
    args = parser.parse_args()
    if not args.confirm_delete_qlvb:
        parser.error("Requires --confirm-delete-qlvb; deletes project data permanently")
    target = parse_dsn(config.PG_DSN)
    if target.get("dbname") != "qlvb" or target.get("host") not in {"localhost", "127.0.0.1"}:
        raise SystemExit("Refusing reset: expected local database qlvb")
    allowed = {"qlvb_docs", "qlvb_docs_v2", "docnexus_agentic_v3", "docnexus_agentic_v4"}
    if config.QDRANT_HOST not in {"localhost", "127.0.0.1"} or config.RAG_COLLECTION not in allowed:
        raise SystemExit("Refusing reset: unexpected Qdrant target")
    paths = [Path(config.CRAWLER_STATE_DB).resolve()] + [
        (Path(config.DOWNLOAD_DIR) / name).resolve()
        for name in ("downloaded_records_di.json", "downloaded_records_den.json")
    ]
    if any(not path.is_relative_to(ROOT / "data") for path in paths):
        raise SystemExit("Refusing reset: crawler state outside project data directory")
    collections = {c.name for c in store._q.get_collections().collections}
    connection = psycopg2.connect(config.PG_DSN, connect_timeout=5)
    try:
        with connection, connection.cursor() as cursor:
            cursor.execute("SELECT current_database()")
            assert cursor.fetchone()[0] == "qlvb"
            cursor.execute("SET LOCAL lock_timeout = '5s'")
            cursor.execute("TRUNCATE documents, document_files, can_cu, document_relations, rag_query_logs RESTART IDENTITY CASCADE")
        print("PostgreSQL qlvb: cleared document data and query logs; schema retained")
    finally:
        connection.close()
    for name in sorted(collections & allowed):
        store._q.delete_collection(name)
        print("Deleted Qdrant collection:", name)
    store.ensure_collection()
    state = paths[0]
    if state.exists():
        connection = sqlite3.connect(state)
        try:
            with connection:
                for table in ("completed_records", "scan_state", "migration_sources"):
                    connection.execute(f"DELETE FROM {table}")
        finally:
            connection.close()
    for path in paths[1:]:
        if path.exists():
            path.unlink()
    print("Crawler checkpoints cleared. Original source files and baseline reports preserved.")
    print("Empty active collection:", config.RAG_COLLECTION)


if __name__ == "__main__":
    main()
