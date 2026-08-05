"""The interchangeability proof for both vector protocols.

Every assertion here runs against every implementation. That is the only thing
standing between a protocol with two implementations and a protocol shaped like
whichever one was written first -- if an assertion has to be relaxed to admit a
new store, the protocol leaked that store's assumptions.

`test_prune_keeps_two_digit_chunks` is the regression this file exists for. A
store that compares `chunk_key` as text places `'chunk_9'` after `'chunk_10'`
and deletes live chunks from every document long enough to have ten of them;
they re-embed on the next run, and the churn never stops.
"""

from __future__ import annotations

import pytest

from m365_brain.index.vector import HashEmbeddingProvider, create_embedding_provider
from m365_brain.index.vector.base import EmbeddingProvider, VectorStore
from m365_brain.index.vector.chunking import chunk_key_for

from .conftest import a_chunk, vector_payload

DIMENSIONS = 8


def vectors(count: int, dimensions: int) -> list[list[float]]:
    """Distinct unit vectors along successive axes -- ordering without a model."""
    return [[1.0 if axis == n % dimensions else 0.0 for axis in range(dimensions)] for n in range(count)]


# -- VectorStore ----------------------------------------------------------


def test_store_satisfies_the_protocol(store):
    assert isinstance(store, VectorStore)


def test_written_chunks_come_back_as_hashes(store, seed):
    entity_id = seed(1)[0]
    chunk = a_chunk(entity_id, 0, "some text")
    store.write_chunks([chunk], vectors(1, DIMENSIONS))
    assert store.chunk_hashes() == {entity_id: {chunk_key_for(0): chunk.content_hash}}


def test_rewriting_a_chunk_replaces_it(store, seed):
    entity_id = seed(1)[0]
    store.write_chunks([a_chunk(entity_id, 0, "first")], vectors(1, DIMENSIONS))
    store.write_chunks([a_chunk(entity_id, 0, "second")], vectors(1, DIMENSIONS))
    hashes = store.chunk_hashes()
    assert hashes[entity_id][chunk_key_for(0)] == a_chunk(entity_id, 0, "second").content_hash
    assert len(hashes[entity_id]) == 1


def test_mismatched_chunk_and_embedding_counts_raise(store, seed):
    entity_id = seed(1)[0]
    with pytest.raises(ValueError):
        store.write_chunks([a_chunk(entity_id, 0, "a")], vectors(2, DIMENSIONS))


def test_clear_empties_the_store(store, seed):
    entity_id = seed(1)[0]
    store.write_chunks([a_chunk(entity_id, 0, "text")], vectors(1, DIMENSIONS))
    store.clear()
    assert store.chunk_hashes() == {}


def test_query_returns_nearest_first_and_honours_k(store, seed):
    entity_id = seed(1)[0]
    chunks = [a_chunk(entity_id, n, f"chunk {n}") for n in range(3)]
    store.write_chunks(chunks, vectors(3, DIMENSIONS))

    hits = store.query(vectors(3, DIMENSIONS)[1], k=2)
    assert len(hits) == 2
    assert hits[0].chunk_key == chunk_key_for(1)
    assert hits[0].distance <= hits[1].distance


def test_prune_removes_every_chunk_of_a_departed_entity(store, seed):
    kept, gone = seed(2)
    store.write_chunks([a_chunk(kept, 0, "kept"), a_chunk(gone, 0, "gone")], vectors(2, DIMENSIONS))

    stats = store.prune(live_entity_ids={kept}, expected_chunk_counts={kept: 1})

    assert stats.stale == 1
    assert set(store.chunk_hashes()) == {kept}


def test_prune_keeps_two_digit_chunks(store, seed):
    """Twelve chunks stay twelve chunks: the count is a number, not a string."""
    entity_id = seed(1)[0]
    chunks = [a_chunk(entity_id, n, f"chunk {n}") for n in range(12)]
    store.write_chunks(chunks, vectors(12, DIMENSIONS))

    stats = store.prune(live_entity_ids={entity_id}, expected_chunk_counts={entity_id: 12})

    assert stats.tail == 0
    assert sorted(store.chunk_hashes()[entity_id]) == sorted(chunk_key_for(n) for n in range(12))


def test_prune_drops_the_tail_when_a_document_shrinks(store, seed):
    entity_id = seed(1)[0]
    store.write_chunks([a_chunk(entity_id, n, f"chunk {n}") for n in range(12)], vectors(12, DIMENSIONS))

    stats = store.prune(live_entity_ids={entity_id}, expected_chunk_counts={entity_id: 1})

    assert stats.tail == 11
    assert list(store.chunk_hashes()[entity_id]) == [chunk_key_for(0)]


def test_pruned_chunks_stop_answering_queries(store, seed):
    entity_id = seed(1)[0]
    store.write_chunks([a_chunk(entity_id, n, f"chunk {n}") for n in range(3)], vectors(3, DIMENSIONS))
    store.prune(live_entity_ids={entity_id}, expected_chunk_counts={entity_id: 1})

    hits = store.query(vectors(3, DIMENSIONS)[2], k=10)

    assert [hit.chunk_key for hit in hits] == [chunk_key_for(0)]


# -- EmbeddingProvider ----------------------------------------------------


@pytest.fixture()
def provider(index_payload) -> EmbeddingProvider:
    index_payload["vector"]["provider"] = "hash"
    return create_embedding_provider(vector_payload(index_payload, "memory"))


def test_provider_satisfies_the_protocol(provider):
    assert isinstance(provider, EmbeddingProvider)


def test_provider_width_matches_config(provider, index_config):
    assert provider.dimensions == index_config.vector.dimensions


def test_embedding_a_query_is_deterministic(provider):
    assert provider.embed_query("same text") == provider.embed_query("same text")


def test_different_text_embeds_differently(provider):
    assert provider.embed_query("one") != provider.embed_query("two")


def test_documents_come_back_in_order_and_at_full_width(provider):
    embeddings = provider.embed_documents(["a", "b", "c"])
    assert len(embeddings) == 3
    assert all(len(embedding) == provider.dimensions for embedding in embeddings)
    assert embeddings[0] == provider.embed_query("a")


def test_a_store_refuses_an_embedding_of_the_wrong_width(store, seed):
    """Whatever the provider claims, the store was created at one width."""
    entity_id = seed(1)[0]
    with pytest.raises(Exception):  # noqa: B017 -- each store reports its own type
        store.write_chunks([a_chunk(entity_id, 0, "text")], [[0.5] * (DIMENSIONS + 1)])


def test_the_fake_provider_takes_its_width_from_config():
    assert HashEmbeddingProvider(3).dimensions == 3
