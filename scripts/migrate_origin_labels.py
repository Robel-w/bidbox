"""Rename legacy origin labels in the local DuckDB store to the live/sandbox scheme.

Idempotent: a store that already uses the new labels is left untouched.
"""
import sys
import time

import duckdb

from bidbox.ingest import ORIGIN_LABEL_TABLES, db_path, migrate_origin_labels


def origin_counts(connection: duckdb.DuckDBPyConnection) -> dict[str, int]:
    rows = connection.execute("SELECT origin, count(*) FROM procurement_records GROUP BY origin ORDER BY origin").fetchall()
    return {row[0]: row[1] for row in rows}


def main() -> int:
    path = db_path()
    if not path.exists():
        print(f"Store not found at {path}; nothing to migrate.", file=sys.stderr)
        return 1
    try:
        connection = duckdb.connect(str(path), read_only=False)
    except duckdb.Error as exc:
        print(f"Store at {path} is locked or unreadable: {exc}", file=sys.stderr)
        return 2
    try:
        tables = {row[0] for row in connection.execute("SELECT table_name FROM information_schema.tables").fetchall()}
        missing = [table for table in ORIGIN_LABEL_TABLES if table not in tables]
        if not {"procurement_records"} <= tables:
            print(f"Store at {path} has no procurement_records table; nothing to migrate.", file=sys.stderr)
            return 1
        if missing:
            print(f"Tables not present (skipped): {', '.join(missing)}")
        before = origin_counts(connection)
        print(f"Before: {before}")
        changed = migrate_origin_labels(connection)
        after = origin_counts(connection)
        print(f"After: {after}")
        print("Migration applied." if changed else "No legacy origin labels found; nothing to change.")
        stale = {origin: count for origin, count in after.items() if origin in ("live-portal", "synthetic")}
        if stale:
            print(f"Legacy origin labels remain: {stale}", file=sys.stderr)
            return 1
        return 0
    finally:
        start = time.monotonic()
        connection.close()
        print(f"Closed store in {time.monotonic() - start:.1f}s")


if __name__ == "__main__":
    raise SystemExit(main())
