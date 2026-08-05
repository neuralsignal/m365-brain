"""Breadth-first traversal of the entity graph. No SQL, no store knowledge.

The algorithm and the edge fetch are deliberately separate. Traversal is where
the interesting behaviour lives -- depth limits, cycle guards, forward
references that point at nothing yet -- and none of it is a property of where
edges are kept. Splitting it out means these functions are tested against the
in-memory fake and would run unchanged over any other backend.

Two backend calls per depth, not per node: the frontier is queried as a set.
That is the difference between one round-trip and several hundred on a
well-connected graph, and it is why `outgoing_relations` and
`incoming_relations` take a sequence rather than an id.
"""

from __future__ import annotations

from typing import Literal

from m365_brain.index.backends.base import IndexBackend
from m365_brain.model import EntityRef, GraphEdge, Observation, RelationEdge


def find(backend: IndexBackend, identifier: str, by_permalink: bool) -> EntityRef | None:
    """Look an entity up by permalink, or by title with alias and partial fallbacks."""
    return backend.find_entity(identifier, by_permalink)


def observations(backend: IndexBackend, entity_id: int) -> list[Observation]:
    """Every observation recorded for an entity."""
    return backend.get_observations(entity_id)


def traverse(backend: IndexBackend, seed_id: int, max_depth: int) -> list[GraphEdge]:
    """Every edge reachable from `seed_id` within `max_depth` hops.

    Edges are returned in discovery order, so an edge's position implies its
    depth. Both directions are followed: a document nobody links *from* is
    still connected to everything that links *to* it, and a traversal that
    ignored incoming edges would report most entities as isolated.

    An outgoing edge whose target is unresolved is still reported, carrying the
    name it points at. Those names are the forward references -- a link written
    before the file it names exists -- and hiding them until resolution would
    make the graph look thinner than the documents say it is.
    """
    visited: set[int] = {seed_id}
    frontier: set[int] = {seed_id}
    edges: list[GraphEdge] = []

    for depth in range(1, max_depth + 1):
        if not frontier:
            break
        ordered = sorted(frontier)
        discovered: set[int] = set()

        for relation in backend.outgoing_relations(ordered):
            edges.append(_edge(relation, depth, "outgoing"))
            _visit(relation.to_entity_id, visited, discovered)

        for relation in backend.incoming_relations(ordered):
            edges.append(_edge(relation, depth, "incoming"))
            _visit(relation.from_entity_id, visited, discovered)

        frontier = discovered

    return edges


def _edge(relation: RelationEdge, depth: int, direction: Literal["outgoing", "incoming"]) -> GraphEdge:
    return GraphEdge(
        depth=depth,
        direction=direction,
        from_entity_id=relation.from_entity_id,
        to_entity_id=relation.to_entity_id,
        to_name=relation.to_name,
        relation_type=relation.relation_type,
    )


def _visit(entity_id: int | None, visited: set[int], discovered: set[int]) -> None:
    """Add an entity to the next frontier once, ever. The cycle guard is this line."""
    if entity_id is not None and entity_id not in visited:
        visited.add(entity_id)
        discovered.add(entity_id)
