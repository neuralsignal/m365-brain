"""DDL for the SQLite index.

Three statement groups, all `IF NOT EXISTS`, so `initialize` is idempotent and
there is no migration framework: the index is derived from files, and a schema
change is answered by re-running the sync with `full_rebuild=True` rather than
by a migration path. Nothing here is a system of record.

`entity_key` carries the UNIQUE constraint rather than `file_path`, because two
configured roots may each hold `projects/x.md`. The key is `{root name}/{path}`
and the root name is validated unique at config load, so uniqueness of the key
is guaranteed by construction.
"""

from __future__ import annotations

import sqlite3

ENTITY_SQL = """
CREATE TABLE IF NOT EXISTS entity (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_key TEXT UNIQUE NOT NULL,
    root_name TEXT NOT NULL,
    file_path TEXT NOT NULL,
    title TEXT NOT NULL,
    type TEXT NOT NULL,
    permalink TEXT UNIQUE NOT NULL,
    tags TEXT,
    aliases TEXT,
    metadata TEXT,
    checksum TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    content TEXT
);

CREATE TABLE IF NOT EXISTS observation (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id INTEGER NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
    category TEXT NOT NULL,
    content TEXT NOT NULL,
    tags TEXT,
    context TEXT
);

CREATE TABLE IF NOT EXISTS relation (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_entity_id INTEGER NOT NULL REFERENCES entity(id) ON DELETE CASCADE,
    to_entity_id INTEGER REFERENCES entity(id) ON DELETE SET NULL,
    to_name TEXT NOT NULL,
    relation_type TEXT NOT NULL,
    context TEXT,
    UNIQUE(from_entity_id, to_name, relation_type)
);

CREATE INDEX IF NOT EXISTS idx_entity_type ON entity(type);
CREATE INDEX IF NOT EXISTS idx_entity_root ON entity(root_name);
CREATE INDEX IF NOT EXISTS idx_entity_updated ON entity(updated_at);
CREATE INDEX IF NOT EXISTS idx_observation_entity ON observation(entity_id);
CREATE INDEX IF NOT EXISTS idx_observation_category ON observation(category);
CREATE INDEX IF NOT EXISTS idx_relation_from ON relation(from_entity_id);
CREATE INDEX IF NOT EXISTS idx_relation_to ON relation(to_entity_id);
CREATE INDEX IF NOT EXISTS idx_relation_type ON relation(relation_type);
"""

# Column order is the contract for bm25() weights and snippet() column indexes.
# `SEARCH_COLUMNS` below is that order, named, so neither is a magic integer.
FTS_SQL = """
CREATE VIRTUAL TABLE IF NOT EXISTS search_index USING fts5(
    title,
    content,
    tags,
    type UNINDEXED,
    file_path UNINDEXED,
    permalink UNINDEXED,
    tokenize='unicode61'
);
"""

CATALOG_SQL = """
CREATE TABLE IF NOT EXISTS file_catalog (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    extractor TEXT NOT NULL,
    original_path TEXT NOT NULL UNIQUE,
    file_name TEXT NOT NULL,
    extension TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    modified_at TEXT NOT NULL,
    conversion_status TEXT NOT NULL,
    output_path TEXT,
    error_message TEXT
);
CREATE INDEX IF NOT EXISTS idx_catalog_extractor ON file_catalog(extractor);
CREATE INDEX IF NOT EXISTS idx_catalog_extension ON file_catalog(extension);
CREATE INDEX IF NOT EXISTS idx_catalog_status ON file_catalog(conversion_status);
CREATE INDEX IF NOT EXISTS idx_catalog_name ON file_catalog(file_name);
CREATE INDEX IF NOT EXISTS idx_catalog_modified ON file_catalog(modified_at DESC);
"""

SEARCH_COLUMNS: tuple[str, ...] = ("title", "content", "tags")


def search_column_index(column: str) -> int:
    """Position of a searchable FTS column, for `snippet()`.

    Raises rather than falling back to column 0: a typo in
    `index.search.snippet.column` would otherwise silently snippet the title of
    every result and look like a ranking bug.
    """
    if column not in SEARCH_COLUMNS:
        raise ValueError(
            f"index.search.snippet.column {column!r} is not a searchable column; expected one of {list(SEARCH_COLUMNS)}"
        )
    return SEARCH_COLUMNS.index(column)


CATALOG_RENAMES: tuple[tuple[str, str], ...] = (("source", "extractor"),)
"""Catalog columns renamed since a database in the wild was created.

The one exception to "no migration framework", and it is narrow on purpose.
Every other table here IS derived from markdown, so a schema change is answered
by `index sync --full`, which rewrites entity/observation/relation/FTS from the
files. `file_catalog` is not: its rows are registered at the storage boundary
while an extractor downloads a binary (`index/catalog_storage.py`), and no sync
ever recreates them. A bare `CREATE TABLE IF NOT EXISTS` with a renamed column
is a no-op on an existing database, so every catalog query would raise
`no such column` until the operator deleted the index -- which loses the rows
for good, because getting them back means re-downloading every attachment.
"""


def _apply_catalog_renames(conn: sqlite3.Connection) -> None:
    """Rename any catalog column still carrying its old name. Idempotent."""
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(file_catalog)")}
    for old, new in CATALOG_RENAMES:
        if old in columns and new not in columns:
            conn.execute(f"ALTER TABLE file_catalog RENAME COLUMN {old} TO {new}")
            # SQLite carries an index across a column rename but keeps its old
            # name, so `CATALOG_SQL` would then add a second index over the same
            # column. Indexes here are named `idx_catalog_<column>`.
            conn.execute(f"DROP INDEX IF EXISTS idx_catalog_{old}")


def initialize(conn: sqlite3.Connection) -> None:
    """Create tables, indexes, the FTS table and the catalog. Safe to repeat."""
    conn.executescript(ENTITY_SQL)
    conn.executescript(FTS_SQL)
    _apply_catalog_renames(conn)
    conn.executescript(CATALOG_SQL)
