# telegram_bot/readers/sqlite_ro.py
"""Read-only SQLite helpers.

All access goes through ?mode=ro URI. Writes raise OperationalError automatically;
the helpers do nothing extra to prevent them — SQLite enforces.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Optional


def _connect_ro(db_path: Path) -> sqlite3.Connection:
    uri = f"file:{db_path}?mode=ro&immutable=0"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def query_one(db_path: Path, sql: str, params: tuple = ()) -> Optional[dict]:
    """Execute, return first row as dict (or None)."""
    with _connect_ro(db_path) as conn:
        row = conn.execute(sql, params).fetchone()
        return dict(row) if row else None


def query_all(db_path: Path, sql: str, params: tuple = ()) -> list[dict]:
    """Execute, return all rows as list[dict]."""
    with _connect_ro(db_path) as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


def max_rowid(db_path: Path, table: str) -> int:
    """Return MAX(id) for an append-only table, or 0 if empty / table missing."""
    try:
        row = query_one(db_path, f"SELECT COALESCE(MAX(id), 0) AS m FROM {table}")
    except sqlite3.OperationalError:
        return 0
    return int(row["m"]) if row else 0
