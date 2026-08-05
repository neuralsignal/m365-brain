"""Traversal, against the fake backend only.

If any of these needed a real database, the split between the algorithm and the
edge fetch would have failed: depth limits, cycle guards and unresolved targets
are properties of the walk, not of where edges are kept.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from m365_brain.config.index import IndexConfig
from m365_brain.index.backends.memory import InMemoryIndexBackend
from m365_brain.index.graph import find, observations, traverse
from m365_brain.model import Entity, Observation, Relation

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


def note(title: str, links_to: list[str]) -> Entity:
    return replace(
        BASE,
        key=f"corpus/{title}.md",
        file_path=f"{title}.md",
        title=title,
        permalink=title.lower(),
        relations=[
            Relation(relation_type="links_to", to_name=target, to_entity_id=None, context=None) for target in links_to
        ],
    )


@pytest.fixture()
def graph(index_payload):
    """`graph(*entities)` -> (backend, {title: entity id}) with relations resolved."""

    def build(*entities: Entity):
        backend = InMemoryIndexBackend(IndexConfig.model_validate(index_payload))
        backend.initialize()
        backend.upsert_entities(entities)
        backend.resolve_relations()
        ids = {
            entity.title: indexed.entity_id
            for entity, indexed in zip(entities, _in_order(backend, entities), strict=True)
        }
        return backend, ids

    return build


def _in_order(backend, entities):
    indexed = backend.indexed_files()
    return [indexed[entity.key] for entity in entities]


def test_a_seed_with_no_relations_yields_nothing(graph):
    backend, ids = graph(note("A", []))
    assert traverse(backend, ids["A"], max_depth=3) == []


def test_an_outgoing_edge_is_found_at_depth_one(graph):
    backend, ids = graph(note("A", ["B"]), note("B", []))
    edges = traverse(backend, ids["A"], max_depth=1)
    assert [(edge.depth, edge.direction, edge.to_name) for edge in edges] == [(1, "outgoing", "B")]


def test_an_incoming_edge_is_found_too(graph):
    """A document nobody links from is still connected to what links to it."""
    backend, ids = graph(note("A", ["B"]), note("B", []))
    edges = traverse(backend, ids["B"], max_depth=1)
    assert [(edge.depth, edge.direction) for edge in edges] == [(1, "incoming")]


def test_depth_limits_the_walk(graph):
    backend, ids = graph(note("A", ["B"]), note("B", ["C"]), note("C", ["D"]), note("D", []))
    assert max(edge.depth for edge in traverse(backend, ids["A"], max_depth=2)) == 2
    assert {edge.to_name for edge in traverse(backend, ids["A"], max_depth=2)} == {"B", "C"}


def test_depth_zero_walks_nowhere(graph):
    backend, ids = graph(note("A", ["B"]), note("B", []))
    assert traverse(backend, ids["A"], max_depth=0) == []


def test_a_cycle_terminates(graph):
    """A visited node never re-enters the frontier, so the walk cannot loop.

    The edges of an already-visited node are still reported when it is *in* the
    frontier -- B's own edges appear at depth 2 -- but B is never queued twice,
    which is what bounds the walk regardless of `max_depth`.
    """
    backend, ids = graph(note("A", ["B"]), note("B", ["A"]))

    edges = traverse(backend, ids["A"], max_depth=100)

    assert max(edge.depth for edge in edges) == 2


def test_a_self_reference_terminates(graph):
    backend, ids = graph(note("A", ["A"]))
    assert max(edge.depth for edge in traverse(backend, ids["A"], max_depth=5)) == 1


def test_an_unresolved_target_still_produces_an_edge(graph):
    """A link written before the file it names is the forward reference, not a gap."""
    backend, ids = graph(note("A", ["Nowhere"]))
    edge = traverse(backend, ids["A"], max_depth=1)[0]
    assert edge.to_entity_id is None
    assert edge.to_name == "Nowhere"


def test_the_frontier_is_queried_once_per_depth(graph, monkeypatch):
    """Two backend calls per depth, not per node -- the batching survives the port."""
    backend, ids = graph(note("A", ["B", "C"]), note("B", ["D"]), note("C", ["D"]), note("D", []))
    calls: list[int] = []
    original = backend.outgoing_relations
    monkeypatch.setattr(
        backend, "outgoing_relations", lambda entity_ids: (calls.append(len(entity_ids)), original(entity_ids))[1]
    )

    traverse(backend, ids["A"], max_depth=2)

    assert calls == [1, 2]


def test_find_looks_up_by_permalink(graph):
    backend, ids = graph(note("A", []))
    assert find(backend, "a", by_permalink=True).entity_id == ids["A"]


def test_find_falls_back_to_the_title(graph):
    backend, ids = graph(note("A", []))
    assert find(backend, "A", by_permalink=False).entity_id == ids["A"]


def test_find_returns_none_for_an_unknown_identifier(graph):
    backend, _ids = graph(note("A", []))
    assert find(backend, "nothing-like-this", by_permalink=True) is None


def test_observations_come_back_for_an_entity(graph):
    entity = replace(BASE, observations=[Observation(category="Note", content="a fact", tags=[], context=None)])
    backend, _ids = graph(entity)
    entity_id = next(iter(backend.indexed_files().values())).entity_id
    assert [o.content for o in observations(backend, entity_id)] == ["a fact"]
