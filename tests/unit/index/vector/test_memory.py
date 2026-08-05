"""The fakes' own behaviour -- what the shared contract deliberately does not claim.

Both facts pinned here are properties of *these* implementations, not of the
protocols: the hash embedder is deterministic-but-meaningless, and the in-memory
store measures Euclidean distance over exact vectors. Pinning them here keeps
them out of the shared contract, where a real store would have to fake them.
"""

from __future__ import annotations

import math

import pytest

from m365_brain.index.vector.chunking import chunk_key_for
from m365_brain.index.vector.memory import HashEmbeddingProvider, InMemoryVectorStore
from m365_brain.model import Chunk


def a_chunk(entity_id: int, index: int, text: str) -> Chunk:
    return Chunk(entity_id=entity_id, chunk_key=chunk_key_for(index), text=text, content_hash=f"hash-{text}")


def test_hash_embeddings_are_unit_length():
    """Distance then depends only on direction, which is what makes ordering stable."""
    vector = HashEmbeddingProvider(16).embed_query("anything")
    assert math.isclose(math.sqrt(sum(value * value for value in vector)), 1.0, rel_tol=1e-9)


def test_hash_embeddings_carry_no_meaning():
    """Near-synonyms are as far apart as unrelated words. No test may assume otherwise."""
    provider = HashEmbeddingProvider(16)
    assert provider.embed_query("cat") != provider.embed_query("kitten")


def test_the_empty_string_still_embeds():
    assert len(HashEmbeddingProvider(4).embed_query("")) == 4


def test_distance_is_euclidean_over_the_stored_vector():
    store = InMemoryVectorStore()
    store.initialize(2)
    store.write_chunks([a_chunk(1, 0, "text")], [[3.0, 4.0]])

    hit = store.query([0.0, 0.0], k=1)[0]

    assert math.isclose(hit.distance, 5.0, rel_tol=1e-9)


def test_writing_a_wrong_width_vector_raises_after_initialize():
    store = InMemoryVectorStore()
    store.initialize(4)
    with pytest.raises(ValueError, match="dimensions"):
        store.write_chunks([a_chunk(1, 0, "text")], [[0.1, 0.2]])


def test_the_fake_never_orphans_an_embedding():
    """Chunk and vector are one dict entry here, so the counter is structurally zero."""
    store = InMemoryVectorStore()
    store.initialize(2)
    store.write_chunks([a_chunk(1, 0, "a"), a_chunk(1, 1, "b")], [[1.0, 0.0], [0.0, 1.0]])

    stats = store.prune(live_entity_ids=set(), expected_chunk_counts={})

    assert (stats.stale, stats.tail, stats.orphan_embeddings) == (2, 0, 0)


def test_ties_break_deterministically():
    store = InMemoryVectorStore()
    store.initialize(2)
    store.write_chunks([a_chunk(2, 0, "a"), a_chunk(1, 0, "b")], [[1.0, 0.0], [1.0, 0.0]])

    hits = store.query([1.0, 0.0], k=2)

    assert [hit.entity_id for hit in hits] == [1, 2]
