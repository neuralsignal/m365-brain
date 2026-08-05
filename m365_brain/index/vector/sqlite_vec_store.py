"""`SqliteVecStore` -- chunks in an ordinary table, embeddings in a `vec0` one.

Two tables rather than one because `vec0` virtual tables carry a vector and a
rowid and little else. The chunk row is the record; the embedding row is the
index entry, joined on `search_vector_chunks.id = search_vector_embeddings.rowid`.

That join is also the failure mode this module is careful about: a delete that
removes a chunk row and forgets its embedding row leaves a vector that no query
can reach and every query has to scan. `prune` reports those separately so the
number is visible rather than inferred.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Mapping, Sequence, Set
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from m365_brain.config.index import SqliteIndexConfig, VectorConfig
from m365_brain.index.vector.chunking import CHUNK_KEY_PREFIX
from m365_brain.model import Chunk, PruneStats, VectorHit

CHUNKS_SQL = """
CREATE TABLE IF NOT EXISTS search_vector_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_id INTEGER NOT NULL,
    chunk_key TEXT NOT NULL,
    chunk_text TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (entity_id) REFERENCES entity(id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX IF NOT EXISTS uix_vector_chunks_entity_key
    ON search_vector_chunks (entity_id, chunk_key);
CREATE INDEX IF NOT EXISTS idx_vector_chunks_entity
    ON search_vector_chunks (entity_id);
"""

# `SUBSTR` is 1-based, so the chunk number starts one past the prefix.
_CHUNK_NUMBER_SQL = f"CAST(SUBSTR(chunk_key, {len(CHUNK_KEY_PREFIX) + 1}) AS INTEGER)"


class SqliteVecStore:
    """The `sqlite-vec` extension over the same database file as the index."""

    def __init__(self, sqlite_config: SqliteIndexConfig, vector_config: VectorConfig) -> None:
        self._path = Path(sqlite_config.path)
        self._busy_timeout_ms = sqlite_config.busy_timeout_ms
        self._journal_mode = sqlite_config.journal_mode
        self._write_batch_size = vector_config.write_batch_size

    @contextmanager
    def connect(self, readonly: bool) -> Iterator[sqlite3.Connection]:
        """A connection with the extension loaded. Public so tests can assert state."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(f"file:{self._path}", uri=True)
        conn.row_factory = sqlite3.Row
        if readonly:
            conn.execute("PRAGMA query_only=ON")
        else:
            conn.execute(f"PRAGMA journal_mode={self._journal_mode}")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(f"PRAGMA busy_timeout={self._busy_timeout_ms}")
        _load_extension(conn)
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

    def initialize(self, dimensions: int) -> None:
        with self.connect(readonly=False) as conn:
            conn.executescript(CHUNKS_SQL)
            conn.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS search_vector_embeddings USING vec0(embedding float[{dimensions}])"
            )

    def close(self) -> None:
        """Nothing to release: connections do not outlive an operation."""
        return None

    def clear(self) -> None:
        with self.connect(readonly=False) as conn:
            conn.execute("DELETE FROM search_vector_embeddings")
            conn.execute("DELETE FROM search_vector_chunks")

    def chunk_hashes(self) -> dict[int, dict[str, str]]:
        with self.connect(readonly=True) as conn:
            rows = conn.execute("SELECT entity_id, chunk_key, source_hash FROM search_vector_chunks").fetchall()
        stored: dict[int, dict[str, str]] = {}
        for row in rows:
            stored.setdefault(row["entity_id"], {})[row["chunk_key"]] = row["source_hash"]
        return stored

    def write_chunks(self, chunks: Sequence[Chunk], embeddings: Sequence[Sequence[float]]) -> None:
        """One short transaction per `index.vector.write_batch_size` chunks.

        A single transaction over a full rebuild holds the write lock for
        minutes, which is a `database is locked` generator for anything else
        touching the file.
        """
        if len(chunks) != len(embeddings):
            raise ValueError(f"write_chunks got {len(chunks)} chunks and {len(embeddings)} embeddings")
        now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        for start in range(0, len(chunks), self._write_batch_size):
            window = slice(start, start + self._write_batch_size)
            with self.connect(readonly=False) as conn:
                for chunk, embedding in zip(chunks[window], embeddings[window], strict=True):
                    _write_one(conn, chunk, embedding, now)

    def prune(self, live_entity_ids: Set[int], expected_chunk_counts: Mapping[int, int]) -> PruneStats:
        with self.connect(readonly=False) as conn:
            stored_ids = {
                row["entity_id"] for row in conn.execute("SELECT DISTINCT entity_id FROM search_vector_chunks")
            }
            stale = _delete_chunks(
                conn,
                [row["id"] for row in _stale_rows(conn, stored_ids - set(live_entity_ids))],
            )
            tail = 0
            for entity_id in sorted(stored_ids & set(live_entity_ids)):
                rows = conn.execute(
                    f"SELECT id FROM search_vector_chunks WHERE entity_id = ? AND {_CHUNK_NUMBER_SQL} >= ?",
                    (entity_id, expected_chunk_counts.get(entity_id, 0)),
                ).fetchall()
                tail += _delete_chunks(conn, [row["id"] for row in rows])

            orphans = conn.execute(
                "SELECT rowid FROM search_vector_embeddings WHERE rowid NOT IN (SELECT id FROM search_vector_chunks)"
            ).fetchall()
            for row in orphans:
                conn.execute("DELETE FROM search_vector_embeddings WHERE rowid = ?", (row["rowid"],))
        return PruneStats(stale=stale, tail=tail, orphan_embeddings=len(orphans))

    def query(self, embedding: Sequence[float], k: int) -> list[VectorHit]:
        with self.connect(readonly=True) as conn:
            rows = conn.execute(
                """WITH matches AS (
                       SELECT rowid, distance FROM search_vector_embeddings
                       WHERE embedding MATCH ? AND k = ?
                   )
                   SELECT c.entity_id, c.chunk_key, m.distance
                   FROM matches m JOIN search_vector_chunks c ON c.id = m.rowid
                   ORDER BY m.distance ASC""",
                (json.dumps(list(embedding)), k),
            ).fetchall()
        return [
            VectorHit(entity_id=row["entity_id"], chunk_key=row["chunk_key"], distance=row["distance"]) for row in rows
        ]


# -- internals ------------------------------------------------------------


def _load_extension(conn: sqlite3.Connection) -> None:
    """Load `sqlite-vec`. An unavailable extension is fatal, never degraded."""
    import sqlite_vec

    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)


def _write_one(conn: sqlite3.Connection, chunk: Chunk, embedding: Sequence[float], now: str) -> None:
    conn.execute(
        """INSERT INTO search_vector_chunks (entity_id, chunk_key, chunk_text, source_hash, updated_at)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT (entity_id, chunk_key) DO UPDATE SET
               chunk_text = excluded.chunk_text,
               source_hash = excluded.source_hash,
               updated_at = excluded.updated_at""",
        (chunk.entity_id, chunk.chunk_key, chunk.text, chunk.content_hash, now),
    )
    row = conn.execute(
        "SELECT id FROM search_vector_chunks WHERE entity_id = ? AND chunk_key = ?",
        (chunk.entity_id, chunk.chunk_key),
    ).fetchone()
    # Replacing a chunk must replace its vector: `vec0` has no upsert, so the
    # old row is deleted first or the rowid insert collides.
    conn.execute("DELETE FROM search_vector_embeddings WHERE rowid = ?", (row["id"],))
    conn.execute(
        "INSERT INTO search_vector_embeddings (rowid, embedding) VALUES (?, ?)",
        (row["id"], json.dumps(list(embedding))),
    )


def _stale_rows(conn: sqlite3.Connection, dead_entity_ids: Set[int]) -> list[sqlite3.Row]:
    if not dead_entity_ids:
        return []
    placeholders = ",".join("?" * len(dead_entity_ids))
    return conn.execute(
        f"SELECT id FROM search_vector_chunks WHERE entity_id IN ({placeholders})",
        tuple(sorted(dead_entity_ids)),
    ).fetchall()


def _delete_chunks(conn: sqlite3.Connection, chunk_ids: Sequence[int]) -> int:
    """Delete chunk rows and their embedding rows together, never one alone."""
    for chunk_id in chunk_ids:
        conn.execute("DELETE FROM search_vector_embeddings WHERE rowid = ?", (chunk_id,))
        conn.execute("DELETE FROM search_vector_chunks WHERE id = ?", (chunk_id,))
    return len(chunk_ids)
