"""The shared `sqlite_connection` context manager: pragmas, transactions, hooks."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from m365_brain.index.backends.sqlite_connect import sqlite_connection


def test_writer_gets_configured_journal_mode(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    with sqlite_connection(db, "wal", 5000, readonly=False, post_connect=None) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_reader_is_query_only(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    with sqlite_connection(db, "wal", 5000, readonly=False, post_connect=None):
        pass
    with sqlite_connection(db, "wal", 5000, readonly=True, post_connect=None) as conn:
        assert conn.execute("PRAGMA query_only").fetchone()[0] == 1


def test_foreign_keys_on_for_both(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    with sqlite_connection(db, "wal", 5000, readonly=False, post_connect=None) as conn:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    with sqlite_connection(db, "wal", 5000, readonly=True, post_connect=None) as conn:
        assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_busy_timeout_comes_from_argument(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    with sqlite_connection(db, "wal", 1234, readonly=False, post_connect=None) as conn:
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 1234


def test_parent_directories_are_created(tmp_path: Path) -> None:
    db = tmp_path / "nested" / "deeper" / "test.db"
    with sqlite_connection(db, "wal", 5000, readonly=False, post_connect=None):
        pass
    assert db.is_file()


def test_failed_write_rolls_back(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    with sqlite_connection(db, "wal", 5000, readonly=False, post_connect=None) as conn:
        conn.execute("CREATE TABLE t (v TEXT)")
        conn.execute("INSERT INTO t VALUES ('keep')")
    with pytest.raises(RuntimeError), sqlite_connection(db, "wal", 5000, readonly=False, post_connect=None) as conn:
        conn.execute("DELETE FROM t")
        raise RuntimeError("boom")
    with sqlite_connection(db, "wal", 5000, readonly=True, post_connect=None) as conn:
        assert conn.execute("SELECT COUNT(*) FROM t").fetchone()[0] == 1


def test_successful_write_commits(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    with sqlite_connection(db, "wal", 5000, readonly=False, post_connect=None) as conn:
        conn.execute("CREATE TABLE t (v TEXT)")
        conn.execute("INSERT INTO t VALUES ('saved')")
    with sqlite_connection(db, "wal", 5000, readonly=True, post_connect=None) as conn:
        assert conn.execute("SELECT v FROM t").fetchone()[0] == "saved"


def test_reader_cannot_write(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    with sqlite_connection(db, "wal", 5000, readonly=False, post_connect=None) as conn:
        conn.execute("CREATE TABLE t (v TEXT)")
    with (
        pytest.raises(sqlite3.OperationalError),
        sqlite_connection(db, "wal", 5000, readonly=True, post_connect=None) as conn,
    ):
        conn.execute("INSERT INTO t VALUES ('nope')")


def test_post_connect_hook_is_called(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    calls: list[sqlite3.Connection] = []
    with sqlite_connection(db, "wal", 5000, readonly=False, post_connect=calls.append):
        pass
    assert len(calls) == 1


def test_row_factory_is_sqlite_row(tmp_path: Path) -> None:
    db = tmp_path / "test.db"
    with sqlite_connection(db, "wal", 5000, readonly=False, post_connect=None) as conn:
        assert conn.row_factory is sqlite3.Row
