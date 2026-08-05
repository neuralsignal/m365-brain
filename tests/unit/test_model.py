"""Tests for `m365_brain.model`.

Two things are worth asserting about a module of pure dataclasses: that every
type is genuinely frozen (a mutable "value" shared between the parser, the
index and the vector store is a bug waiting for a long afternoon), and that
none of them acquired a default -- a defaulted field is a value the caller
forgot to supply, silently.
"""

from __future__ import annotations

import dataclasses
import inspect

import pytest

from m365_brain import model
from m365_brain.model import (
    CatalogEntry,
    Chunk,
    Entity,
    EntityRef,
    GraphEdge,
    Observation,
    PruneStats,
    Relation,
    RelationEdge,
    SearchHit,
    SearchPage,
    SyncStats,
    VectorHit,
    VectorSyncStats,
)

ALL_TYPES = [
    obj
    for _, obj in inspect.getmembers(model, inspect.isclass)
    if dataclasses.is_dataclass(obj) and obj.__module__ == model.__name__
]


def test_module_declares_types():
    assert len(ALL_TYPES) >= 15


@pytest.mark.parametrize("dataclass_type", ALL_TYPES, ids=lambda t: t.__name__)
def test_every_type_is_frozen(dataclass_type):
    assert dataclass_type.__dataclass_params__.frozen


@pytest.mark.parametrize("dataclass_type", ALL_TYPES, ids=lambda t: t.__name__)
def test_no_field_has_a_default(dataclass_type):
    defaulted = [
        field.name
        for field in dataclasses.fields(dataclass_type)
        if field.default is not dataclasses.MISSING or field.default_factory is not dataclasses.MISSING
    ]
    assert defaulted == [], f"{dataclass_type.__name__} fields with defaults: {defaulted}"


def _observation() -> Observation:
    return Observation(category="Note", content="a thing happened", tags=["x"], context=None)


def _relation() -> Relation:
    return Relation(relation_type="relates_to", to_name="Other", to_entity_id=None, context=None)


def _entity(key: str) -> Entity:
    return Entity(
        key=key,
        root_name=key.split("/", 1)[0],
        file_path=key.split("/", 1)[1],
        title="A note",
        entity_type="note",
        permalink="a-note",
        tags=["x"],
        aliases=[],
        content="body",
        checksum="abc",
        metadata={"k": 1},
        created_at="2026-08-05T00:00:00Z",
        updated_at="2026-08-05T00:00:00Z",
        observations=[_observation()],
        relations=[_relation()],
    )


def test_entity_round_trips():
    entity = _entity("vault/projects/x.md")

    assert entity.root_name == "vault"
    assert entity.file_path == "projects/x.md"
    assert entity.observations[0].category == "Note"
    assert entity.relations[0].to_entity_id is None


def test_entity_cannot_be_mutated():
    entity = _entity("vault/projects/x.md")

    with pytest.raises(dataclasses.FrozenInstanceError):
        entity.title = "other"


def test_keys_from_two_roots_do_not_collide():
    """The reason `Entity.key` carries the root name at all."""
    first = _entity("personal/projects/x.md")
    second = _entity("shared/projects/x.md")

    assert first.key != second.key
    assert first.file_path == second.file_path


def test_unresolved_relation_edge_keeps_its_target_name():
    edge = RelationEdge(
        from_entity_id=1,
        to_entity_id=None,
        to_name="Not Yet Written",
        relation_type="relates_to",
        context=None,
    )

    assert edge.to_entity_id is None
    assert edge.to_name == "Not Yet Written"


def test_graph_edge_carries_depth_and_direction():
    edge = GraphEdge(
        depth=2,
        direction="incoming",
        from_entity_id=7,
        to_entity_id=1,
        to_name="A note",
        relation_type="links_to",
    )

    assert (edge.depth, edge.direction) == (2, "incoming")


def test_catalog_entry_is_unsaved_when_id_is_none():
    entry = CatalogEntry(
        entry_id=None,
        original_path="/tmp/report.pdf",
        file_name="report.pdf",
        extension=".pdf",
        source="onedrive",
        size_bytes=10,
        modified_at="2026-08-05T00:00:00Z",
        conversion_status="pending",
        output_path=None,
        error=None,
    )

    assert entry.entry_id is None


def test_search_page_composes_hits():
    ref = EntityRef(
        entity_id=1,
        key="vault/a.md",
        title="A",
        entity_type="note",
        permalink="a",
        file_path="a.md",
        updated_at="2026-08-05T00:00:00Z",
    )
    page = SearchPage(hits=[SearchHit(entity=ref, score=1.5, snippet=None)], total=1, page=1, page_size=20)

    assert page.hits[0].entity.entity_id == 1


def test_vector_types():
    chunk = Chunk(entity_id=1, chunk_key="chunk_10", text="t", content_hash="h")
    hit = VectorHit(entity_id=1, chunk_key="chunk_10", distance=0.25)

    assert chunk.chunk_key == hit.chunk_key


def test_stats_types_compose():
    prune = PruneStats(stale=1, tail=2, orphan_embeddings=3)
    vector_stats = VectorSyncStats(
        entities=10,
        chunks_embedded=4,
        chunks_written=4,
        pruned=prune,
        elapsed_seconds=0.5,
    )
    sync_stats = SyncStats(total=10, indexed=4, skipped=6, pruned=0, resolved=2, errors=0, elapsed_seconds=0.25)

    assert vector_stats.pruned.tail == 2
    assert sync_stats.skipped == 6


def test_model_imports_nothing_from_the_package():
    """Layer 1: `model` sits below everything, including `config`."""
    source = inspect.getsource(model)
    assert "from m365_brain" not in source
    assert "import m365_brain" not in source
