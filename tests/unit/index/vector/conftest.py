"""Fixtures for the vector tests.

`store` is parametrized over every `VectorStore` implementation, and `seed`
exists because one of them is not free-standing: the SQL store keeps chunks in
the same database as the entities they point at, with a foreign key, so ids it
has never seen cannot be written. `seed(n)` produces `n` usable entity ids in
whichever way the store under test requires, and every assertion above it is
then identical.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import Any

import pytest

from m365_brain.config.index import IndexConfig
from m365_brain.index.backends.sqlite import SqliteIndexBackend
from m365_brain.index.vector import create_vector_store
from m365_brain.index.vector.chunking import chunk_key_for
from m365_brain.model import Chunk, Entity
from m365_brain.parsers.text import content_hash

SeedEntities = Callable[[int], list[int]]

BASE_ENTITY = Entity(
    key="corpus/seed.md",
    root_name="corpus",
    file_path="seed.md",
    title="Seed",
    entity_type="note",
    permalink="seed",
    tags=[],
    aliases=[],
    content="seed body",
    checksum="sum-seed",
    metadata={},
    created_at="2026-01-01T00:00:00Z",
    updated_at="2026-01-01T00:00:00Z",
    observations=[],
    relations=[],
)


def a_chunk(entity_id: int, index: int, text: str) -> Chunk:
    """A chunk whose hash matches its text, as the sync always builds them."""
    return Chunk(
        entity_id=entity_id,
        chunk_key=chunk_key_for(index),
        text=text,
        content_hash=content_hash(text),
    )


def vector_payload(index_payload: dict[str, Any], store_name: str) -> IndexConfig:
    index_payload["vector"]["store"] = store_name
    return IndexConfig.model_validate(index_payload)


@pytest.fixture(params=["sqlite_vec", "memory"])
def store_and_seed(request, index_payload):
    config = vector_payload(index_payload, request.param)
    store = create_vector_store(config)
    store.initialize(config.vector.dimensions)

    if request.param == "memory":

        def seed(count: int) -> list[int]:
            return list(range(1, count + 1))

    else:
        backend = SqliteIndexBackend(config)
        backend.initialize()

        def seed(count: int) -> list[int]:
            backend.upsert_entities(
                [
                    replace(BASE_ENTITY, key=f"corpus/seed-{n}.md", permalink=f"seed-{n}", file_path=f"seed-{n}.md")
                    for n in range(count)
                ]
            )
            return sorted(indexed.entity_id for indexed in backend.indexed_files().values())

    yield store, seed
    store.close()


@pytest.fixture()
def store(store_and_seed):
    return store_and_seed[0]


@pytest.fixture()
def seed(store_and_seed) -> SeedEntities:
    return store_and_seed[1]
