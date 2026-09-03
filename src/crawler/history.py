from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import unicodedata
from collections.abc import Iterable


def source_key(document_ref: str, issued_date: str = "", subject: str = "") -> str:
    normalized_ref = unicodedata.normalize("NFKC", document_ref or "").upper()
    normalized_ref = re.sub(r"\s+", "", normalized_ref)
    normalized_date = re.sub(
        r"\s+",
        "",
        unicodedata.normalize("NFKC", issued_date or ""),
    )
    if normalized_ref and normalized_date:
        return f"ref:{normalized_ref}|date:{normalized_date}"
    if normalized_ref:
        return f"ref:{normalized_ref}"
    identity = "|".join(
        re.sub(r"\s+", " ", unicodedata.normalize("NFKC", value or "").strip().lower())
        for value in (normalized_date, subject)
    )
    return f"sha256:{hashlib.sha256(identity.encode('utf-8')).hexdigest()}"

class CrawlHistory:
    """Persistent, indexed crawl checkpoint with one SQLite file handle per run."""

    def __init__(self, path: str):
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        self.connection = sqlite3.connect(path, timeout=30)
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA synchronous=NORMAL")
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS completed_records (
                   direction TEXT NOT NULL,
                   source_key TEXT NOT NULL,
                   document_ref TEXT,
                   completed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                   PRIMARY KEY (direction, source_key)
               )"""
        )
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS migration_sources (
                   path TEXT PRIMARY KEY,
                   file_size INTEGER NOT NULL,
                   mtime_ns INTEGER NOT NULL,
                   imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
               )"""
        )
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS scan_state (
                   direction TEXT PRIMARY KEY,
                   reached_end INTEGER NOT NULL DEFAULT 0,
                   updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
               )"""
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()

    def contains(self, direction: str, key: str) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM completed_records WHERE direction = ? AND source_key = ?",
            (direction, key),
        ).fetchone()
        return row is not None

    def mark_completed(self, records: Iterable[tuple[str, str, str]]) -> None:
        self.connection.executemany(
            """INSERT INTO completed_records(direction, source_key, document_ref)
               VALUES (?, ?, ?)
               ON CONFLICT(direction, source_key) DO UPDATE SET
                   document_ref = excluded.document_ref,
                   completed_at = CURRENT_TIMESTAMP""",
            records,
        )
        self.connection.commit()

    def has_reached_end(self, direction: str) -> bool:
        row = self.connection.execute(
            "SELECT reached_end FROM scan_state WHERE direction = ?",
            (direction,),
        ).fetchone()
        return bool(row and row[0])

    def mark_reached_end(self, direction: str) -> None:
        self._set_reached_end(direction, True)

    def mark_incomplete(self, direction: str) -> None:
        self._set_reached_end(direction, False)

    def _set_reached_end(self, direction: str, reached_end: bool) -> None:
        self.connection.execute(
            """INSERT INTO scan_state(direction, reached_end) VALUES (?, ?)
               ON CONFLICT(direction) DO UPDATE SET
                    reached_end = excluded.reached_end,
                    updated_at = CURRENT_TIMESTAMP""",
            (direction, int(reached_end)),
        )
        self.connection.commit()

    def import_legacy_json(self, path: str, direction: str) -> int:
        if not os.path.exists(path):
            return 0
        try:
            stat = os.stat(path)
            migrated = self.connection.execute(
                "SELECT 1 FROM migration_sources WHERE path = ? AND file_size = ? AND mtime_ns = ?",
                (path, stat.st_size, stat.st_mtime_ns),
            ).fetchone()
            if migrated:
                return 0
            with open(path, "r", encoding="utf-8") as stream:
                references = json.load(stream)
        except (OSError, json.JSONDecodeError):
            return 0
        rows = [
            (direction, source_key(str(reference)), str(reference))
            for reference in references
            if str(reference).strip()
        ]
        before = self.connection.total_changes
        self.connection.executemany(
            """INSERT OR IGNORE INTO completed_records
               (direction, source_key, document_ref) VALUES (?, ?, ?)""",
            rows,
        )
        imported = self.connection.total_changes - before
        self.connection.execute(
            """INSERT INTO migration_sources(path, file_size, mtime_ns)
               VALUES (?, ?, ?)
               ON CONFLICT(path) DO UPDATE SET
                   file_size = excluded.file_size,
                   mtime_ns = excluded.mtime_ns,
                   imported_at = CURRENT_TIMESTAMP""",
            (path, stat.st_size, stat.st_mtime_ns),
        )
        self.connection.commit()
        return imported
