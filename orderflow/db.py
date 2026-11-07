"""SQLite connection + explicit transaction helper."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from . import ids

SCHEMA_PATH = Path(__file__).parent / "sql" / "schema.sql"


def connect(db_path) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), isolation_level=None)  # we manage transactions explicitly
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    # deterministic ID helpers usable from SQL, so fact builds stay set-based
    conn.create_function("order_line_key", 2, ids.order_line_key, deterministic=True)
    conn.create_function("date_key", 1, ids.date_key, deterministic=True)
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))


@contextmanager
def transaction(conn: sqlite3.Connection):
    """BEGIN ... COMMIT, or ROLLBACK on any exception (all-or-nothing loads)."""
    conn.execute("BEGIN")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")
