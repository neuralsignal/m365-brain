"""The three search paths, entirely against the fakes.

No SQLite here on purpose. What `search` decides -- which mode needs vectors,
what a similarity floor discards, how a long document collapses to one row, what
happens when a filter cannot be honoured -- is the same whatever the store is,
and testing it through a real database would only mean also testing FTS5.

The in-memory backend matches `fts` as a case-insensitive substring, so these
tests use whole words. Nothing here may assume FTS5 semantics.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from m365_brain.config.index import IndexConfig
from m365_brain.index.backends.memory import InMemoryIndexBackend
from m365_brain.index.query import parse_metadata_filter
from m365_brain.index.search import SearchFilters, search
from m365_brain.index.vector import HashEmbeddingProvider, InMemoryVectorStore
from m365_brain.index.vector.chunking import chunk_key_for
from m365_brain.model import Chunk, Entity

NO_FILTERS = SearchFilters(entity_type=None, tag=None, metadata=())

BASE = Entity(
    key="corpus/a.md",
    root_name="corpus",
    file_path="a.md",
    title="A",
    entity_type="note",
    permalink="a",
    tags=[],
    aliases=[],
    content="body",
    checksum="sum",
    metadata={},
    created_at="2026-01-01T00:00:00Z",
    updated_at="2026-01-01T00:00:00Z",
    observations=[],
    relations=[],
)


def note(title: str, **fields) -> Entity:
    return replace(
        BASE, key=f"corpus/{title}.md", file_path=f"{title}.md", title=title, permalink=title.lower(), **fields
    )


class Harness:
    """A configured index plus a vector store, wired to `search` by one call."""

    def __init__(self, config: IndexConfig) -> None:
        self.config = config
        self.backend = InMemoryIndexBackend(config)
        self.backend.initialize()
        self.provider = HashEmbeddingProvider(config.vector.dimensions)
        self.store = InMemoryVectorStore()
        self.store.initialize(config.vector.dimensions)

    def add(self, *entities: Entity) -> dict[str, int]:
        self.backend.upsert_entities(entities)
        self.backend.rebuild_text_index()
        indexed = self.backend.indexed_files()
        return {entity.title: indexed[entity.key].entity_id for entity in entities}

    def embed(self, entity_id: int, texts: list[str]) -> None:
        chunks = [
            Chunk(entity_id=entity_id, chunk_key=chunk_key_for(n), text=text, content_hash=f"h{n}")
            for n, text in enumerate(texts)
        ]
        self.store.write_chunks(chunks, self.provider.embed_documents(texts))

    def run(self, text, mode, filters=NO_FILTERS, page=1, vectors=True):
        return search(
            self.config,
            self.backend,
            self.provider if vectors else None,
            self.store if vectors else None,
            text,
            mode,
            filters,
            page,
            self.config.search.page_size,
        )


@pytest.fixture()
def harness(index_payload) -> Harness:
    return Harness(IndexConfig.model_validate(index_payload))


# -- text -----------------------------------------------------------------


def test_text_search_finds_matching_content(harness):
    harness.add(note("Alpha", content="rhubarb"), note("Beta", content="custard"))
    page = harness.run("rhubarb", mode="text")
    assert [hit.entity.title for hit in page.hits] == ["Alpha"]


def test_text_search_with_no_query_lists_everything(harness):
    harness.add(note("Alpha"), note("Beta"))
    assert harness.run(None, mode="text").total == 2


def test_a_type_filter_narrows_the_text_path(harness):
    harness.add(note("Alpha", entity_type="memo"), note("Beta", entity_type="note"))
    page = harness.run(None, mode="text", filters=SearchFilters(entity_type="memo", tag=None, metadata=()))
    assert [hit.entity.title for hit in page.hits] == ["Alpha"]


def test_a_tag_filter_narrows_the_text_path(harness):
    harness.add(note("Alpha", tags=["urgent"]), note("Beta", tags=["later"]))
    page = harness.run(None, mode="text", filters=SearchFilters(entity_type=None, tag="urgent", metadata=()))
    assert [hit.entity.title for hit in page.hits] == ["Alpha"]


def test_a_metadata_filter_narrows_the_text_path(harness):
    harness.add(note("Alpha", metadata={"priority": "5"}), note("Beta", metadata={"priority": "1"}))
    filters = SearchFilters(entity_type=None, tag=None, metadata=(parse_metadata_filter("priority>=3"),))
    page = harness.run(None, mode="text", filters=filters)
    assert [hit.entity.title for hit in page.hits] == ["Alpha"]


def test_text_pagination_reports_the_full_total(harness):
    harness.config = replace_page_size(harness.config, 1)
    harness.add(note("Alpha"), note("Beta"))
    page = harness.run(None, mode="text", page=2)
    assert (page.total, page.page, len(page.hits)) == (2, 2, 1)


def replace_page_size(config: IndexConfig, page_size: int) -> IndexConfig:
    payload = config.model_dump()
    payload["search"]["page_size"] = page_size
    return IndexConfig.model_validate(payload)


# -- vector ---------------------------------------------------------------


def test_vector_search_returns_the_nearest_entity(harness):
    ids = harness.add(note("Alpha"), note("Beta"))
    harness.embed(ids["Alpha"], ["rhubarb crumble"])
    harness.embed(ids["Beta"], ["engine maintenance"])

    page = harness.run("rhubarb crumble", mode="vector")

    assert page.hits[0].entity.title == "Alpha"


def test_a_long_document_collapses_to_its_best_chunk(harness):
    """Otherwise one document fills the page with itself."""
    ids = harness.add(note("Alpha"))
    harness.embed(ids["Alpha"], ["rhubarb crumble", "rhubarb crumble again", "something else"])

    page = harness.run("rhubarb crumble", mode="vector")

    assert page.total == 1
    assert len(page.hits) == 1


def test_the_similarity_floor_discards_distant_matches(harness):
    """The hash embedder puts unrelated text far away, which is the point here."""
    ids = harness.add(note("Alpha"))
    harness.embed(ids["Alpha"], ["completely unrelated text"])

    assert harness.run("rhubarb crumble", mode="vector").total == 0


def test_a_perfect_match_survives_the_floor(harness):
    ids = harness.add(note("Alpha"))
    harness.embed(ids["Alpha"], ["rhubarb crumble"])
    assert harness.run("rhubarb crumble", mode="vector").total == 1


def test_a_type_filter_narrows_the_vector_path(harness):
    ids = harness.add(note("Alpha", entity_type="memo"), note("Beta", entity_type="note"))
    harness.embed(ids["Alpha"], ["shared text"])
    harness.embed(ids["Beta"], ["shared text"])

    page = harness.run("shared text", mode="vector", filters=SearchFilters(entity_type="note", tag=None, metadata=()))

    assert [hit.entity.title for hit in page.hits] == ["Beta"]


def test_a_chunk_whose_entity_is_gone_is_dropped(harness):
    """Hydration is the join; an id it cannot resolve is a stale chunk, not a hit."""
    ids = harness.add(note("Alpha"))
    harness.embed(ids["Alpha"], ["rhubarb crumble"])
    harness.backend.delete_entities(["corpus/Alpha.md"])

    assert harness.run("rhubarb crumble", mode="vector").total == 0


@pytest.mark.parametrize("mode", ["vector", "hybrid"])
def test_vector_modes_need_query_text(harness, mode):
    with pytest.raises(ValueError, match="nothing to embed"):
        harness.run(None, mode=mode)


@pytest.mark.parametrize("mode", ["vector", "hybrid"])
def test_a_tag_filter_is_refused_rather_than_dropped(harness, mode):
    """A filtered search that quietly returns unfiltered results is the worse failure."""
    with pytest.raises(ValueError, match="tag"):
        harness.run("anything", mode=mode, filters=SearchFilters(entity_type=None, tag="urgent", metadata=()))


@pytest.mark.parametrize("mode", ["vector", "hybrid"])
def test_a_metadata_filter_is_refused_rather_than_dropped(harness, mode):
    filters = SearchFilters(entity_type=None, tag=None, metadata=(parse_metadata_filter("priority>=3"),))
    with pytest.raises(ValueError, match="metadata"):
        harness.run("anything", mode=mode, filters=filters)


# -- hybrid ---------------------------------------------------------------


def test_hybrid_returns_both_halves(harness):
    ids = harness.add(note("Alpha", content="rhubarb"), note("Beta", content="custard"))
    harness.embed(ids["Beta"], ["custard tart"])

    page = harness.run("custard tart", mode="hybrid")

    assert {hit.entity.title for hit in page.hits} >= {"Beta"}


def test_hybrid_keeps_the_snippet_the_text_half_produced(harness):
    ids = harness.add(note("Alpha", content="rhubarb"))
    harness.embed(ids["Alpha"], ["rhubarb"])
    page = harness.run("rhubarb", mode="hybrid")
    assert page.hits[0].snippet == harness.run("rhubarb", mode="text").hits[0].snippet


def test_hybrid_ranks_agreement_first(harness):
    """The whole reason to fuse: on both lists beats top of one."""
    ids = harness.add(note("Alpha", content="rhubarb pie"), note("Beta", content="rhubarb"))
    harness.embed(ids["Beta"], ["rhubarb"])

    page = harness.run("rhubarb", mode="hybrid")

    assert page.hits[0].entity.title == "Beta"


def test_hybrid_deduplicates(harness):
    ids = harness.add(note("Alpha", content="rhubarb"))
    harness.embed(ids["Alpha"], ["rhubarb"])
    page = harness.run("rhubarb", mode="hybrid")
    assert len(page.hits) == len({hit.entity.entity_id for hit in page.hits})


# -- guards ---------------------------------------------------------------


@pytest.mark.parametrize("mode", ["vector", "hybrid"])
def test_disabled_vectors_raise_naming_the_config_key(index_payload, mode):
    index_payload["vector"]["enabled"] = False
    harness = Harness(IndexConfig.model_validate(index_payload))
    with pytest.raises(ValueError, match="index.vector.enabled"):
        harness.run("anything", mode=mode)


@pytest.mark.parametrize("mode", ["vector", "hybrid"])
def test_missing_vector_objects_raise_rather_than_falling_back(harness, mode):
    with pytest.raises(ValueError, match="index.vector.enabled"):
        harness.run("anything", mode=mode, vectors=False)


def test_text_search_still_works_with_vectors_disabled(index_payload):
    index_payload["vector"]["enabled"] = False
    harness = Harness(IndexConfig.model_validate(index_payload))
    harness.add(note("Alpha"))
    assert harness.run(None, mode="text", vectors=False).total == 1


def test_an_unknown_mode_raises(harness):
    with pytest.raises(ValueError, match="unknown search mode"):
        harness.run("anything", mode="telepathy")
