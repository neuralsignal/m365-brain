"""The SQLite connection contract: pragmas, transactions, init caching.

These are the guarantees `test_base.py` cannot see. They are the reason the
adapter is a separate module from its SQL: every one of them is about *how* a
connection is opened, not about what is stored.
"""

from __future__ import annotations

import sqlite3

import pytest

from m365_brain.config.index import IndexConfig
from m365_brain.index.backends.sqlite import SqliteIndexBackend
from tests.unit.index.conftest import an_entity


def build(index_payload: dict) -> SqliteIndexBackend:
    index_payload["backend"] = "sqlite"
    return SqliteIndexBackend(IndexConfig.model_validate(index_payload))


def pragma(backend: SqliteIndexBackend, name: str, readonly: bool):
    with backend.connect(readonly=readonly) as conn:
        return conn.execute(f"PRAGMA {name}").fetchone()[0]


def test_writers_use_the_configured_journal_mode(index_payload):
    backend = build(index_payload)
    backend.initialize()
    assert pragma(backend, "journal_mode", readonly=False) == "wal"


def test_journal_mode_comes_from_config(index_payload):
    index_payload["sqlite"]["journal_mode"] = "DELETE"
    backend = build(index_payload)
    backend.initialize()
    assert pragma(backend, "journal_mode", readonly=False) == "delete"


def test_readers_are_query_only(index_payload):
    backend = build(index_payload)
    backend.initialize()
    assert pragma(backend, "query_only", readonly=True) == 1
    assert pragma(backend, "query_only", readonly=False) == 0


def test_foreign_keys_are_on_for_both(index_payload):
    backend = build(index_payload)
    backend.initialize()
    assert pragma(backend, "foreign_keys", readonly=True) == 1
    assert pragma(backend, "foreign_keys", readonly=False) == 1


def test_busy_timeout_comes_from_config(index_payload):
    index_payload["sqlite"]["busy_timeout_ms"] = 1234
    backend = build(index_payload)
    backend.initialize()
    assert pragma(backend, "busy_timeout", readonly=False) == 1234


def test_a_reader_cannot_write(index_payload):
    backend = build(index_payload)
    backend.initialize()
    with pytest.raises(sqlite3.OperationalError), backend.connect(readonly=True) as conn:
        conn.execute("DELETE FROM entity")


def test_a_failed_write_rolls_back(index_payload):
    backend = build(index_payload)
    backend.initialize()
    backend.upsert_entities([an_entity()])
    with pytest.raises(RuntimeError), backend.connect(readonly=False) as conn:
        conn.execute("DELETE FROM entity")
        raise RuntimeError("boom")
    assert len(backend.indexed_files()) == 1


def test_initialize_is_idempotent_across_instances(index_payload):
    first = build(index_payload)
    first.initialize()
    first.upsert_entities([an_entity()])

    second = build(index_payload)
    second.initialize()  # same file, must not wipe or fail
    assert set(second.indexed_files()) == {"corpus/note.md"}


def test_initialization_state_is_per_instance(index_payload):
    """Not a module global: one that survived a test case is why the previous
    incarnation of this code needed tests to reach in and reset it."""
    first = build(index_payload)
    first.initialize()
    second = build(index_payload)
    assert second._initialized is False


def test_the_database_file_and_its_parent_are_created(index_payload, tmp_path):
    index_payload["sqlite"]["path"] = str(tmp_path / "nested" / "deeper" / "index.db")
    backend = build(index_payload)
    backend.initialize()
    assert (tmp_path / "nested" / "deeper" / "index.db").is_file()


def test_close_is_safe_to_repeat(index_payload):
    backend = build(index_payload)
    backend.initialize()
    backend.close()
    backend.close()
    assert backend.indexed_files() == {}
