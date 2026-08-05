"""The knowledge layer's data types. Frozen dataclasses, no behaviour.

Layer 1: this module imports nothing from the package -- not `config`, not
`models` (which is the unrelated SQL table set for the admin database). It is
the vocabulary that the parsers, the index backends, the vector store and the
facade all agree on, which only works if none of them can bend it.

`Entity.key` is the identity that everything else hangs off, and it carries the
root name: `"{root name}/{root-relative posix path}"`. Two configured roots may
each hold `projects/x.md`, and without the prefix the second one collides with
the first on a unique-path constraint.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

# --------------------------------------------------------------------------
# Parsed documents
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Observation:
    """One `- [category] text #tag (context)` line."""

    category: str
    content: str
    tags: list[str]
    context: str | None


@dataclass(frozen=True, slots=True)
class Relation:
    """One edge out of an entity. `to_entity_id` is None until resolution."""

    relation_type: str
    to_name: str
    to_entity_id: int | None
    context: str | None


@dataclass(frozen=True, slots=True)
class Entity:
    """One markdown file, parsed."""

    key: str
    root_name: str
    file_path: str
    title: str
    entity_type: str
    permalink: str
    tags: list[str]
    aliases: list[str]
    content: str
    checksum: str
    metadata: dict[str, Any]
    created_at: str
    updated_at: str
    observations: list[Observation]
    relations: list[Relation]


# --------------------------------------------------------------------------
# Index reads
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EntityRef:
    """An indexed entity without its body -- what reads return."""

    entity_id: int
    key: str
    title: str
    entity_type: str
    permalink: str
    file_path: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class IndexedFile:
    """What the index already knows about a file, for checksum comparison."""

    entity_id: int
    checksum: str


@dataclass(frozen=True, slots=True)
class RelationEdge:
    """A stored edge. `to_entity_id` is None for an unresolved forward reference."""

    from_entity_id: int
    to_entity_id: int | None
    to_name: str
    relation_type: str
    context: str | None


@dataclass(frozen=True, slots=True)
class GraphEdge:
    """An edge as a traversal reports it, carrying the depth it was found at."""

    depth: int
    direction: Literal["outgoing", "incoming"]
    from_entity_id: int
    to_entity_id: int | None
    to_name: str
    relation_type: str


# --------------------------------------------------------------------------
# File catalog
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    """A non-markdown source file and its conversion state.

    `entry_id` is None before the row exists.
    """

    entry_id: int | None
    original_path: str
    file_name: str
    extension: str
    source: str
    size_bytes: int
    modified_at: str
    conversion_status: str
    output_path: str | None
    error: str | None


@dataclass(frozen=True, slots=True)
class CatalogQuery:
    """Catalog filters as one value, so no caller assembles SQL."""

    extension: str | None
    source: str | None
    status: str | None
    modified_after: str | None
    name_contains: str | None
    limit: int


# --------------------------------------------------------------------------
# Vectors
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Chunk:
    """One embeddable slice of an entity's text."""

    entity_id: int
    chunk_key: str
    text: str
    content_hash: str


@dataclass(frozen=True, slots=True)
class VectorHit:
    entity_id: int
    chunk_key: str
    distance: float


# --------------------------------------------------------------------------
# Search
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SearchHit:
    entity: EntityRef
    score: float
    snippet: str | None


@dataclass(frozen=True, slots=True)
class SearchPage:
    hits: list[SearchHit]
    total: int
    page: int
    page_size: int


# --------------------------------------------------------------------------
# Run statistics
# --------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SyncStats:
    total: int
    indexed: int
    skipped: int
    pruned: int
    resolved: int
    errors: int
    elapsed_seconds: float


@dataclass(frozen=True, slots=True)
class PruneStats:
    """Why chunks went away. Separate counters because the three causes are
    different bugs when the number is wrong."""

    stale: int
    tail: int
    orphan_embeddings: int


@dataclass(frozen=True, slots=True)
class VectorSyncStats:
    entities: int
    chunks_embedded: int
    chunks_written: int
    pruned: PruneStats
    elapsed_seconds: float
