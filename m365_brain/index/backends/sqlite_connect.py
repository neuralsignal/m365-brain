"""Shared SQLite connection context manager.

Both `SqliteIndexBackend` and `SqliteVecStore` need the same pragma setup and
transaction lifecycle. This module is the single source of truth for that logic.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def sqlite_connection(
    path: Path,
    journal_mode: str,
    busy_timeout_ms: int,
    readonly: bool,
    post_connect: Callable[[sqlite3.Connection], None] | None,
) -> Iterator[sqlite3.Connection]:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(f"file:{path}", uri=True)
    conn.row_factory = sqlite3.Row
    if readonly:
        conn.execute("PRAGMA query_only=ON")
    else:
        conn.execute(f"PRAGMA journal_mode={journal_mode}")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute(f"PRAGMA busy_timeout={busy_timeout_ms}")
    if post_connect is not None:
        post_connect(conn)
    try:
        yield conn
        if not readonly:
            conn.commit()
    except Exception:
        if not readonly:
            conn.rollback()
        raise
    finally:
        conn.close()
