"""What only the SQL store can be asked: two tables, and the gap between them.

The shared contract covers everything the fakes can also answer. Everything
here is about the fact that a chunk row and its embedding row are separate: an
embedding whose chunk is gone answers no query and is scanned by all of them,
and it is invisible unless something counts it.
"""

from __future__ import annotations

import json

import pytest

from m365_brain.config.index import IndexConfig
from m365_brain.index.backends.sqlite import SqliteIndexBackend
from m365_brain.index.vector.chunking import chunk_key_for
from m365_brain.index.vector.sqlite_vec_store import SqliteVecStore

from .conftest import BASE_ENTITY, a_chunk

DIMENSIONS = 8


@pytest.fixture()
def config(index_payload) -> IndexConfig:
    index_payload["vector"]["store"] = "sqlite_vec"
    return IndexConfig.model_validate(index_payload)


@pytest.fixture()
def entity_id(config) -> int:
    backend = SqliteIndexBackend(config)
    backend.initialize()
    backend.upsert_entities([BASE_ENTITY])
    return next(iter(backend.indexed_files().values())).entity_id


@pytest.fixture()
def store(config) -> SqliteVecStore:
    instance = SqliteVecStore(config.sqlite, config.vector)
    instance.initialize(config.vector.dimensions)
    yield instance
    instance.close()


def a_vector(seed: int) -> list[float]:
    return [1.0 if axis == seed % DIMENSIONS else 0.0 for axis in range(DIMENSIONS)]


def count(store: SqliteVecStore, table: str) -> int:
    with store.connect(readonly=True) as conn:
        return int(conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"])


def test_initialize_is_idempotent(config, store):
    """A second instance over the same file must not fail on existing tables."""
    again = SqliteVecStore(config.sqlite, config.vector)
    again.initialize(config.vector.dimensions)
    assert count(store, "search_vector_chunks") == 0


def test_every_chunk_row_has_exactly_one_embedding_row(store, entity_id):
    store.write_chunks([a_chunk(entity_id, n, f"c{n}") for n in range(5)], [a_vector(n) for n in range(5)])
    assert count(store, "search_vector_chunks") == count(store, "search_vector_embeddings") == 5


def test_rewriting_a_chunk_replaces_its_embedding_rather_than_adding_one(store, entity_id):
    store.write_chunks([a_chunk(entity_id, 0, "first")], [a_vector(0)])
    store.write_chunks([a_chunk(entity_id, 0, "second")], [a_vector(1)])
    assert count(store, "search_vector_embeddings") == 1


def test_writing_more_than_a_batch_still_writes_everything(config, store, entity_id):
    """The batch size splits transactions, not the work."""
    total = config.vector.write_batch_size * 2 + 3
    store.write_chunks([a_chunk(entity_id, n, f"c{n}") for n in range(total)], [a_vector(n) for n in range(total)])
    assert count(store, "search_vector_chunks") == total


def test_tail_prune_takes_the_embedding_rows_with_it(store, entity_id):
    store.write_chunks([a_chunk(entity_id, n, f"c{n}") for n in range(12)], [a_vector(n) for n in range(12)])

    store.prune(live_entity_ids={entity_id}, expected_chunk_counts={entity_id: 1})

    assert count(store, "search_vector_chunks") == count(store, "search_vector_embeddings") == 1


def test_orphaned_embeddings_are_removed(store, entity_id):
    """Rows left by an earlier prune that deleted chunks and not their vectors."""
    store.write_chunks([a_chunk(entity_id, 0, "c0")], [a_vector(0)])
    with store.connect(readonly=False) as conn:
        conn.execute(
            "INSERT INTO search_vector_embeddings (rowid, embedding) VALUES (?, ?)",
            (999_999, json.dumps(a_vector(3))),
        )

    stats = store.prune(live_entity_ids={entity_id}, expected_chunk_counts={entity_id: 1})

    assert stats.orphan_embeddings == 1
    assert count(store, "search_vector_embeddings") == 1


def test_clear_empties_both_tables(store, entity_id):
    store.write_chunks([a_chunk(entity_id, n, f"c{n}") for n in range(3)], [a_vector(n) for n in range(3)])
    store.clear()
    assert count(store, "search_vector_chunks") == count(store, "search_vector_embeddings") == 0


def test_query_reports_the_chunk_key_of_the_nearest_row(store, entity_id):
    store.write_chunks([a_chunk(entity_id, n, f"c{n}") for n in range(3)], [a_vector(n) for n in range(3)])
    hits = store.query(a_vector(2), k=1)
    assert hits[0].chunk_key == chunk_key_for(2)
    assert hits[0].entity_id == entity_id


def test_readers_cannot_write(store):
    with store.connect(readonly=True) as conn:
        assert conn.execute("PRAGMA query_only").fetchone()[0] == 1
