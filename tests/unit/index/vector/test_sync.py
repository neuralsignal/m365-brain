"""Vector sync: what it re-embeds, and what it removes.

Two of these are regressions with a history. A second run over an unchanged
corpus must embed nothing; when the prune compared chunk keys as text it
deleted live chunks from every document with ten or more, which then re-embedded
forever. And a document that shrinks must lose its tail chunks, or the store
keeps answering queries with text the file no longer contains.

They run against both stores, because the bug was in one of them and the
protocol is the thing that must make it impossible in either.
"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from m365_brain.config.index import IndexConfig
from m365_brain.index.backends.sqlite import SqliteIndexBackend
from m365_brain.index.vector import HashEmbeddingProvider, create_vector_store, sync_vectors
from m365_brain.index.vector.chunking import chunk_key_for, split_into_chunks

ENTITY_IDS = (1, 2, 3)


class FakeTextBackend:
    """Only the one method vector sync needs: `(entity id, text)` pairs."""

    def __init__(self, texts: dict[int, str]) -> None:
        self.texts = texts

    def iter_indexed_text(self):
        return iter(sorted(self.texts.items()))


class CountingProvider:
    """A hash embedder that records how many texts it was asked to embed."""

    def __init__(self, dimensions: int) -> None:
        self._inner = HashEmbeddingProvider(dimensions)
        self.embedded: list[int] = []

    @property
    def dimensions(self) -> int:
        return self._inner.dimensions

    def embed_query(self, text: str) -> list[float]:
        return self._inner.embed_query(text)

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        self.embedded.append(len(texts))
        return self._inner.embed_documents(texts)


LONG_DOCUMENT = "\n\n".join(f"# Section {n}\n\nParagraph {n} " + "x" * 800 for n in range(15))


@pytest.fixture(params=["sqlite_vec", "memory"])
def harness(request, index_payload):
    """`(config, store, provider)` for one store implementation.

    The SQL store's chunks carry a foreign key to `entity`, so the ids these
    tests use have to exist before a chunk may name one. The fake has no such
    constraint, which is exactly the difference the parametrization exercises.
    """
    index_payload["vector"]["store"] = request.param
    config = IndexConfig.model_validate(index_payload)
    store = create_vector_store(config)
    if request.param == "sqlite_vec":
        _seed_entity_rows(config)
    provider = CountingProvider(config.vector.dimensions)
    yield config, store, provider
    store.close()


def _seed_entity_rows(config: IndexConfig) -> None:
    backend = SqliteIndexBackend(config)
    backend.initialize()
    with backend.connect(readonly=False) as conn:
        for entity_id in ENTITY_IDS:
            conn.execute(
                """INSERT INTO entity
                       (id, entity_key, root_name, file_path, title, type, permalink, checksum, created_at, updated_at)
                   VALUES (?, ?, 'corpus', ?, ?, 'note', ?, 'sum', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')""",
                (entity_id, f"corpus/{entity_id}.md", f"{entity_id}.md", f"Note {entity_id}", f"note-{entity_id}"),
            )


def run(harness, texts: dict[int, str], full_rebuild: bool):
    config, store, provider = harness
    return sync_vectors(config.vector, FakeTextBackend(texts), provider, store, full_rebuild)


def test_a_first_run_embeds_every_chunk(harness):
    stats = run(harness, {1: "a short note"}, full_rebuild=False)
    assert stats.entities == 1
    assert stats.chunks_embedded == 1


def test_a_second_run_over_unchanged_text_embeds_nothing(harness):
    """The regression: pruning must not delete chunks the next run has to redo."""
    _config, _store, provider = harness
    texts = {1: LONG_DOCUMENT, 2: "a short note"}

    first = run(harness, texts, full_rebuild=False)
    second = run(harness, texts, full_rebuild=False)

    assert first.chunks_embedded >= 10  # the document must exercise two-digit keys
    assert second.chunks_embedded == 0
    assert provider.embedded == [first.chunks_embedded]


def test_stored_chunks_stay_contiguous(harness):
    config, store, _provider = harness
    run(harness, {1: LONG_DOCUMENT}, full_rebuild=False)
    run(harness, {1: LONG_DOCUMENT}, full_rebuild=False)

    expected = split_into_chunks(LONG_DOCUMENT, config.vector.chunk_size, config.vector.chunk_overlap)
    assert sorted(store.chunk_hashes()[1]) == sorted(chunk_key_for(n) for n in range(len(expected)))


def test_only_the_changed_entity_is_re_embedded(harness):
    run(harness, {1: "first note", 2: "second note"}, full_rebuild=False)
    stats = run(harness, {1: "first note", 2: "second note, edited"}, full_rebuild=False)
    assert stats.chunks_embedded == 1


def test_a_shrinking_document_loses_its_tail(harness):
    _config, store, _provider = harness
    run(harness, {1: LONG_DOCUMENT}, full_rebuild=False)

    stats = run(harness, {1: "now tiny"}, full_rebuild=False)

    assert stats.pruned.tail >= 10
    assert list(store.chunk_hashes()[1]) == [chunk_key_for(0)]


def test_a_departed_entity_loses_every_chunk(harness):
    _config, store, _provider = harness
    run(harness, {1: "kept note", 2: "doomed note"}, full_rebuild=False)

    stats = run(harness, {1: "kept note"}, full_rebuild=False)

    assert stats.pruned.stale == 1
    assert set(store.chunk_hashes()) == {1}


def test_full_rebuild_re_embeds_everything(harness):
    texts = {1: "a note"}
    run(harness, texts, full_rebuild=False)
    stats = run(harness, texts, full_rebuild=True)
    assert stats.chunks_embedded == 1


def test_an_empty_index_is_not_an_error(harness):
    stats = run(harness, {}, full_rebuild=False)
    assert (stats.entities, stats.chunks_embedded) == (0, 0)


def test_a_provider_returning_the_wrong_count_crashes(harness):
    """The pairing with embeddings is positional and cannot be recovered from."""
    config, store, _provider = harness

    class ShortProvider:
        dimensions = config.vector.dimensions

        def embed_query(self, text: str) -> list[float]:
            raise NotImplementedError

        def embed_documents(self, texts):
            return []

    with pytest.raises(ValueError, match="positional"):
        sync_vectors(config.vector, FakeTextBackend({1: "a note"}), ShortProvider(), store, full_rebuild=False)
