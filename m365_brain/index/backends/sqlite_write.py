"""The SQLite write path: upsert, delete, resolve, reindex.

Functions take a connection rather than owning one. `SqliteIndexBackend` decides
transaction boundaries -- one short transaction per sync batch -- because a
single sync-long write lock is what produces "database is locked" for every
other process touching the file.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence

from m365_brain.model import Entity, IndexedFile


def indexed_files(conn: sqlite3.Connection) -> dict[str, IndexedFile]:
    rows = conn.execute("SELECT id, entity_key, checksum FROM entity").fetchall()
    return {r["entity_key"]: IndexedFile(entity_id=r["id"], checksum=r["checksum"]) for r in rows}


def permalink_owners(conn: sqlite3.Connection) -> dict[str, str]:
    rows = conn.execute("SELECT permalink, entity_key FROM entity").fetchall()
    return {r["permalink"]: r["entity_key"] for r in rows}


def upsert_entities(conn: sqlite3.Connection, entities: Sequence[Entity]) -> None:
    for entity in entities:
        entity_id = _upsert_one(conn, entity)
        _write_children(conn, entity_id, entity)


def _upsert_one(conn: sqlite3.Connection, entity: Entity) -> int:
    values = (
        entity.root_name,
        entity.file_path,
        entity.title,
        entity.entity_type,
        entity.permalink,
        _json_or_null(entity.tags),
        _json_or_null(entity.aliases),
        _json_or_null(entity.metadata),
        entity.checksum,
        entity.created_at,
        entity.updated_at,
        entity.content,
    )
    existing = conn.execute("SELECT id FROM entity WHERE entity_key = ?", (entity.key,)).fetchone()
    if existing:
        entity_id = existing["id"]
        conn.execute(
            """UPDATE entity SET root_name=?, file_path=?, title=?, type=?, permalink=?, tags=?,
                   aliases=?, metadata=?, checksum=?, created_at=?, updated_at=?, content=?
               WHERE id=?""",
            (*values, entity_id),
        )
        # Children are replaced wholesale: an observation removed from the file
        # has no id to update, so a diff would leave it behind forever.
        conn.execute("DELETE FROM observation WHERE entity_id=?", (entity_id,))
        conn.execute("DELETE FROM relation WHERE from_entity_id=?", (entity_id,))
        return entity_id

    cursor = conn.execute(
        """INSERT INTO entity (entity_key, root_name, file_path, title, type, permalink, tags,
               aliases, metadata, checksum, created_at, updated_at, content)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (entity.key, *values),
    )
    return int(cursor.lastrowid)


def _write_children(conn: sqlite3.Connection, entity_id: int, entity: Entity) -> None:
    conn.executemany(
        "INSERT INTO observation (entity_id, category, content, tags, context) VALUES (?, ?, ?, ?, ?)",
        [(entity_id, o.category, o.content, _json_or_null(o.tags), o.context) for o in entity.observations],
    )
    # OR IGNORE: the UNIQUE(from, to_name, type) constraint is the de-duplication
    # the parser already performs, kept as a database-level guarantee.
    conn.executemany(
        """INSERT OR IGNORE INTO relation (from_entity_id, to_entity_id, to_name, relation_type, context)
           VALUES (?, NULL, ?, ?, ?)""",
        [(entity_id, r.to_name, r.relation_type, r.context) for r in entity.relations],
    )


def delete_entities(conn: sqlite3.Connection, entity_keys: Sequence[str]) -> int:
    if not entity_keys:
        return 0
    placeholders = ",".join("?" * len(entity_keys))
    cursor = conn.execute(f"DELETE FROM entity WHERE entity_key IN ({placeholders})", tuple(entity_keys))
    return cursor.rowcount


def resolve_relations(conn: sqlite3.Connection) -> int:
    """Point unresolved edges at a matching entity. Returns how many were resolved.

    Counted by unresolved-before minus unresolved-after rather than by the
    UPDATE's rowcount: the statement touches every unresolved row, including
    those whose subquery yields NULL, so the rowcount answers "how many were
    attempted", which is not a useful number.
    """
    before = _unresolved_count(conn)
    conn.execute(
        """UPDATE relation SET to_entity_id = (
               SELECT e.id FROM entity e
               WHERE e.title = relation.to_name
                  OR e.permalink = relation.to_name
                  OR (e.aliases IS NOT NULL AND EXISTS (
                      SELECT 1 FROM json_each(e.aliases) j WHERE j.value = relation.to_name
                  ))
               LIMIT 1
           )
           WHERE to_entity_id IS NULL"""
    )
    return before - _unresolved_count(conn)


def _unresolved_count(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT COUNT(*) AS n FROM relation WHERE to_entity_id IS NULL").fetchone()["n"])


def rebuild_text_index(conn: sqlite3.Connection) -> None:
    """Rebuild the FTS table from stored entities.

    The searchable content is the concatenated observations **and** the document
    body. Indexing the body closes the recall hole for prose-only files that
    carry no observation lines at all; the bm25 title weight keeps curated facts
    ranking above incidental prose.
    """
    conn.execute("DELETE FROM search_index")
    conn.execute(
        """INSERT INTO search_index (rowid, title, content, tags, type, file_path, permalink)
           SELECT e.id, e.title,
                  COALESCE(
                      (SELECT GROUP_CONCAT(o.category || ': ' || o.content, ' | ')
                       FROM observation o WHERE o.entity_id = e.id),
                      ''
                  ) || ' ' || COALESCE(e.content, ''),
                  COALESCE(e.tags, ''),
                  e.type, e.file_path, e.permalink
           FROM entity e"""
    )


def _json_or_null(value: list[str] | dict[str, object]) -> str | None:
    return json.dumps(value) if value else None
