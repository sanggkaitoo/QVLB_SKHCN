from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core import store


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Áp dụng migration PostgreSQL theo thứ tự tên file.")
    parser.add_argument("--directory", type=Path, default=Path("db/migrations"))
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    migrations = sorted(args.directory.glob("*.sql"))
    if not migrations:
        raise SystemExit(f"Không tìm thấy migration trong {args.directory}")

    with store.pg() as connection, connection.cursor() as cursor:
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                name TEXT PRIMARY KEY,
                checksum TEXT NOT NULL,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )
        """)
        cursor.execute("SELECT name, checksum FROM schema_migrations")
        applied = dict(cursor.fetchall())
        for migration in migrations:
            sql = migration.read_text(encoding="utf-8")
            checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
            if migration.name in applied:
                if applied[migration.name] != checksum:
                    raise RuntimeError(f"Migration đã áp dụng nhưng checksum thay đổi: {migration.name}")
                print(f"[skip] {migration.name}")
                continue
            print(f"[apply] {migration.name}")
            cursor.execute(sql)
            cursor.execute(
                "INSERT INTO schema_migrations(name, checksum) VALUES (%s, %s)",
                (migration.name, checksum),
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
