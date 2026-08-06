"""The `IndexBackend` protocol -- the only thing above it that knows a store exists.

Every method is domain-level. There is deliberately **no** method returning a
connection, a cursor, or a SQL string: the moment one exists, callers write
their own queries, the protocol becomes decoration, and swapping the store
becomes a rewrite instead of a config value.

The query types live here rather than beside the query parser because the
protocol is their contract. A parser turns user text into a `TextQuery`; each
adapter renders it in its own dialect -- `json_extract` for SQLite, `jsonb`
elsewhere, dict lookups in the fake. No caller assembles SQL.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

from m365_brain.model import (
    CatalogEntry,
    CatalogQuery,
    Entity,
    EntityRef,
    IndexedFile,
    Observation,
    RelationEdge,
    SearchPage,
)


@dataclass(frozen=True, slots=True)
class MetadataFilter:
    """One comparison against a metadata key. Dotted keys address nested values."""

    key: str
    op: Literal["eq", "gt", "gte", "lt", "lte", "in", "between"]
    values: tuple[str | float, ...]


@dataclass(frozen=True, slots=True)
class TextQuery:
    """A full-text request. `fts` is None for a filter-only listing."""

    fts: str | None
    entity_type: str | None
    tag: str | None
    metadata: tuple[MetadataFilter, ...]
    page: int
    page_size: int


@runtime_checkable
class IndexBackend(Protocol):
    """Storage for parsed entities, their edges, and the file catalog."""

    def initialize(self) -> None:
        """Create whatever the store needs. Idempotent, per instance."""
        ...

    def close(self) -> None:
        """Release resources. Safe to call twice."""
        ...

    # -- write path, driven by index/sync.py ------------------------------

    def indexed_files(self) -> dict[str, IndexedFile]:
        """`{entity key: (id, checksum)}` for everything currently indexed."""
        ...

    def permalink_owners(self) -> dict[str, str]:
        """`{permalink: entity key}`. One bulk read; the sync resolves collisions in memory."""
        ...

    def upsert_entities(self, entities: Sequence[Entity]) -> None:
        """Insert or replace by entity key, including observations and edges."""
        ...

    def delete_entities(self, entity_keys: Sequence[str]) -> int:
        """Remove entities and their observations and outgoing edges. Returns the count.

        Edges *pointing at* a deleted entity survive with `to_entity_id` cleared:
        the link is still written in a file somewhere, and dropping it would make
        a re-created target silently unreachable.
        """
        ...

    def resolve_relations(self) -> int:
        """Point unresolved edges at entities matching title, permalink, or alias."""
        ...

    def rebuild_text_index(self) -> None:
        """Rebuild the text index from stored entities. Search reflects the last call."""
        ...

    # -- read path, driven by index/{graph,search}.py ----------------------

    def find_entity(self, identifier: str, by_permalink: bool) -> EntityRef | None:
        """Exact permalink, or title -> alias -> partial-title, in that order."""
        ...

    def get_observations(self, entity_id: int) -> list[Observation]:
        """Every observation stored for an entity."""
        ...

    def outgoing_relations(self, entity_ids: Sequence[int]) -> list[RelationEdge]:
        """Edges leaving any of these entities. One call per traversal depth, not per node."""
        ...

    def incoming_relations(self, entity_ids: Sequence[int]) -> list[RelationEdge]:
        """Edges arriving at any of these entities."""
        ...

    def text_search(self, query: TextQuery) -> SearchPage:
        """One page of matches, plus the unpaginated total."""
        ...

    def recent_entities(self, updated_since: str, entity_type: str | None, limit: int) -> list[EntityRef]:
        """Entities updated at or after an ISO timestamp, newest first.

        `entity_type` narrows *before* the limit. It used to be applied by the
        caller afterwards, which made `--type task --limit 20` mean "the tasks
        among the twenty most recent entities of any type" while reading as
        "the twenty most recent tasks".
        """
        ...

    def count_recent_entities(self, updated_since: str, entity_type: str | None) -> int:
        """How many entities `recent_entities` would return without a limit."""
        ...

    def hydrate(self, entity_ids: Sequence[int]) -> dict[int, EntityRef]:
        """Turn ids from a vector or fusion result back into entities."""
        ...

    def iter_indexed_text(self) -> Iterator[tuple[int, str]]:
        """`(entity id, text)` for every indexed entity -- the vector chunker's input."""
        ...

    # -- file catalog, driven by index/catalog.py --------------------------

    def upsert_catalog_entry(self, entry: CatalogEntry) -> int:
        """Insert or update by `original_path`. Returns the row id."""
        ...

    def search_catalog(self, query: CatalogQuery) -> list[CatalogEntry]:
        """Catalog rows matching every set filter, newest modification first."""
        ...

    def count_catalog(self, query: CatalogQuery) -> int:
        """How many rows match every set filter. `query.limit` is ignored.

        The limit is what this count exists to see past: a listing that returns
        `query.limit` rows says nothing about whether that was all of them.
        """
        ...

    def get_catalog_entry(self, original_path: str) -> CatalogEntry | None:
        """One catalog row by its source path."""
        ...

    def get_catalog_entry_by_id(self, entry_id: int) -> CatalogEntry | None:
        """One catalog row by id."""
        ...

    def set_catalog_status(self, original_path: str, state: str, output_path: str | None, error: str | None) -> None:
        """Move a row to a conversion state, replacing `output_path` and `error`.

        One method rather than a `mark_converted` / `mark_failed` pair: those two
        differed by a literal status string, which both duplicated the transition
        logic and hardcoded the vocabulary the config now owns.
        """
        ...

    def remove_catalog_entry(self, original_path: str) -> bool:
        """Delete a row. True when one existed."""
        ...

    def catalog_stats(self) -> dict[str, int]:
        """`total` plus one count per configured conversion state, zeros included."""
        ...
