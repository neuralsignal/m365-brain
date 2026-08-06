"""Schema shape and the one thing that is not DDL: the snippet column index."""

from __future__ import annotations

import sqlite3

import pytest

from m365_brain.index.backends import sqlite_schema as schema


@pytest.fixture()
def conn():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    schema.initialize(connection)
    yield connection
    connection.close()


def tables(conn) -> set[str]:
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()}


def columns(conn, table: str) -> set[str]:
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def test_initialize_creates_every_table(conn):
    assert {"entity", "observation", "relation", "search_index", "file_catalog"} <= tables(conn)


def test_initialize_is_repeatable(conn):
    schema.initialize(conn)
    assert "entity" in tables(conn)


INSERT_ENTITY = (
    "INSERT INTO entity (entity_key, root_name, file_path, title, type, permalink, checksum,"
    " created_at, updated_at)"
    " VALUES (?, ?, ?, 'T', 'note', ?, 'sum', '2026-01-01', '2026-01-01')"
)


def test_entity_key_carries_uniqueness_not_file_path(conn):
    """Two roots may hold the same relative path; only the key is unique."""
    conn.execute(INSERT_ENTITY, ("a/x.md", "a", "x.md", "p-a"))
    conn.execute(INSERT_ENTITY, ("b/x.md", "b", "x.md", "p-b"))
    assert conn.execute("SELECT COUNT(*) FROM entity").fetchone()[0] == 2


def test_duplicate_entity_key_is_rejected(conn):
    conn.execute(INSERT_ENTITY, ("a/x.md", "a", "x.md", "p-1"))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(INSERT_ENTITY, ("a/x.md", "a", "x.md", "p-2"))


def test_duplicate_permalink_is_rejected(conn):
    conn.execute(INSERT_ENTITY, ("a/x.md", "a", "x.md", "same"))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(INSERT_ENTITY, ("b/y.md", "b", "y.md", "same"))


def test_entity_carries_content_and_aliases(conn):
    assert {"content", "aliases", "entity_key", "root_name"} <= columns(conn, "entity")


def test_catalog_columns_match_the_model(conn):
    assert columns(conn, "file_catalog") == {
        "id",
        "extractor",
        "original_path",
        "file_name",
        "extension",
        "size_bytes",
        "modified_at",
        "conversion_status",
        "output_path",
        "error_message",
    }


OLD_CATALOG_SQL = """
CREATE TABLE file_catalog (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    original_path TEXT NOT NULL UNIQUE,
    file_name TEXT NOT NULL,
    extension TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    modified_at TEXT NOT NULL,
    conversion_status TEXT NOT NULL,
    output_path TEXT,
    error_message TEXT
);
CREATE INDEX idx_catalog_source ON file_catalog(source);
"""
"""`file_catalog` exactly as it was created before the column was renamed."""


@pytest.fixture()
def legacy_conn():
    """A database carrying one catalogued row under the old column name."""
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(OLD_CATALOG_SQL)
    connection.execute(
        "INSERT INTO file_catalog (source, original_path, file_name, extension, size_bytes,"
        " modified_at, conversion_status) VALUES ('email', 'a/r.pdf', 'r.pdf', '.pdf', 3, '2026-01-01', 'pending')"
    )
    yield connection
    connection.close()


def test_an_existing_catalog_is_renamed_rather_than_stranded(legacy_conn):
    """The rows survive the rename -- nothing else can put them back.

    `CREATE TABLE IF NOT EXISTS` is a no-op on a database that already has the
    table, so without this every catalog query on an existing index would raise
    `no such column: extractor` forever. Deleting the index is not a recovery:
    catalog rows are registered while an extractor downloads a binary, and no
    sync rebuilds them.
    """
    schema.initialize(legacy_conn)

    row = legacy_conn.execute("SELECT extractor, file_name FROM file_catalog").fetchone()
    assert (row["extractor"], row["file_name"]) == ("email", "r.pdf")
    assert "source" not in columns(legacy_conn, "file_catalog")


def test_the_rename_leaves_one_index_on_the_column(legacy_conn):
    """SQLite carries an index across a rename under its old name; two would remain."""
    schema.initialize(legacy_conn)
    schema.initialize(legacy_conn)

    names = {row["name"] for row in legacy_conn.execute("PRAGMA index_list(file_catalog)")}
    assert "idx_catalog_source" not in names
    assert "idx_catalog_extractor" in names


def test_search_column_index_maps_names_to_positions():
    assert schema.search_column_index("title") == 0
    assert schema.search_column_index("content") == 1
    assert schema.search_column_index("tags") == 2


def test_search_column_index_rejects_an_unsearchable_column():
    with pytest.raises(ValueError, match="permalink"):
        schema.search_column_index("permalink")
