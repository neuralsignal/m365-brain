"""`InMemoryIndexBackend` -- the fake that keeps the protocol honest.

It ships in the package, not in `tests/`: downstream consumers need it for their
own suites, and a protocol with one implementation drifts into that
implementation's shape within two commits.

Two behaviours are copied deliberately rather than improved on, because the
shared contract test asserts them against both backends: text search reflects
the **last `rebuild_text_index()`** rather than the current entities (both
stores keep a derived index, and pretending otherwise would hide a missing
rebuild in the sync), and deleting an entity clears `to_entity_id` on edges
pointing at it rather than deleting them, matching `ON DELETE SET NULL`.

What it does *not* claim is FTS5 semantics. `TextQuery.fts` is split on
whitespace and every token must appear as a case-insensitive substring: `AND`,
`OR`, `NOT` and brackets are ignored rather than obeyed, so `NOT` does not
exclude and a quoted phrase is matched word by word. No test may assume more.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, replace

from m365_brain.config.index import IndexConfig
from m365_brain.index.backends.base import MetadataFilter, TextQuery
from m365_brain.index.backends.filters import catalog_matches, evaluate
from m365_brain.model import (
    CatalogEntry,
    CatalogQuery,
    Entity,
    EntityRef,
    IndexedFile,
    Observation,
    RelationEdge,
    SearchHit,
    SearchPage,
)

_IGNORED_FTS_TOKENS = frozenset({"and", "or", "not", "(", ")", ""})


def _search_tokens(fts: str) -> list[str]:
    """Casefolded substrings to look for, with FTS5 decoration removed.

    `TextQuery.fts` carries FTS5 syntax. A fake that took `telesc*` literally
    would match nothing for anything routed through `index/search.py`, which is
    silence rather than an error.
    """
    tokens = (token.strip('*()"').casefold() for token in fts.split())
    return [token for token in tokens if token not in _IGNORED_FTS_TOKENS]


@dataclass
class _Edge:
    from_entity_id: int
    to_entity_id: int | None
    to_name: str
    relation_type: str
    context: str | None

    def frozen(self) -> RelationEdge:
        return RelationEdge(
            from_entity_id=self.from_entity_id,
            to_entity_id=self.to_entity_id,
            to_name=self.to_name,
            relation_type=self.relation_type,
            context=self.context,
        )


class InMemoryIndexBackend:
    """Dicts keyed by entity id. No persistence, no SQL, no threads."""

    def __init__(self, config: IndexConfig) -> None:
        self._config = config
        self._entities: dict[int, Entity] = {}
        self._ids: dict[str, int] = {}
        self._edges: list[_Edge] = []
        self._text: dict[int, tuple[str, str, str]] = {}
        self._catalog: dict[str, CatalogEntry] = {}
        self._next_entity_id = 1
        self._next_catalog_id = 1

    def initialize(self) -> None:
        return None

    def close(self) -> None:
        return None

    # -- write path -------------------------------------------------------

    def indexed_files(self) -> dict[str, IndexedFile]:
        return {
            entity.key: IndexedFile(entity_id=entity_id, checksum=entity.checksum)
            for entity_id, entity in self._entities.items()
        }

    def permalink_owners(self) -> dict[str, str]:
        return {entity.permalink: entity.key for entity in self._entities.values()}

    def upsert_entities(self, entities: Sequence[Entity]) -> None:
        for entity in entities:
            entity_id = self._ids.get(entity.key)
            if entity_id is None:
                entity_id = self._next_entity_id
                self._next_entity_id += 1
                self._ids[entity.key] = entity_id
            else:
                self._edges = [edge for edge in self._edges if edge.from_entity_id != entity_id]
            self._entities[entity_id] = entity
            self._edges.extend(
                _Edge(entity_id, None, relation.to_name, relation.relation_type, relation.context)
                for relation in entity.relations
            )

    def delete_entities(self, entity_keys: Sequence[str]) -> int:
        deleted = 0
        for key in entity_keys:
            entity_id = self._ids.pop(key, None)
            if entity_id is None:
                continue
            del self._entities[entity_id]
            self._edges = [edge for edge in self._edges if edge.from_entity_id != entity_id]
            for edge in self._edges:
                if edge.to_entity_id == entity_id:
                    edge.to_entity_id = None
            deleted += 1
        return deleted

    def resolve_relations(self) -> int:
        lookup: dict[str, int] = {}
        for entity_id, entity in self._entities.items():
            for name in (entity.title, entity.permalink, *entity.aliases):
                lookup.setdefault(name, entity_id)
        resolved = 0
        for edge in self._edges:
            if edge.to_entity_id is not None:
                continue
            target = lookup.get(edge.to_name)
            if target is not None:
                edge.to_entity_id = target
                resolved += 1
        return resolved

    def rebuild_text_index(self) -> None:
        self._text = {
            entity_id: (
                entity.title,
                " | ".join(f"{o.category}: {o.content}" for o in entity.observations) + " " + entity.content,
                " ".join(entity.tags),
            )
            for entity_id, entity in self._entities.items()
        }

    # -- read path --------------------------------------------------------

    def find_entity(self, identifier: str, by_permalink: bool) -> EntityRef | None:
        if by_permalink:
            return self._first(lambda e: e.permalink == identifier)
        folded = identifier.casefold()
        return (
            self._first(lambda e: e.title.casefold() == folded)
            or self._first(lambda e: any(a.casefold() == folded for a in e.aliases))
            or self._first(lambda e: folded in e.title.casefold())
        )

    def get_observations(self, entity_id: int) -> list[Observation]:
        entity = self._entities.get(entity_id)
        return list(entity.observations) if entity else []

    def outgoing_relations(self, entity_ids: Sequence[int]) -> list[RelationEdge]:
        wanted = set(entity_ids)
        return [edge.frozen() for edge in self._edges if edge.from_entity_id in wanted]

    def incoming_relations(self, entity_ids: Sequence[int]) -> list[RelationEdge]:
        wanted = set(entity_ids)
        return [edge.frozen() for edge in self._edges if edge.to_entity_id in wanted]

    def text_search(self, query: TextQuery) -> SearchPage:
        matched = [
            entity_id
            for entity_id, row in sorted(self._text.items())
            if entity_id in self._entities and self._matches(entity_id, row, query)
        ]
        start = (query.page - 1) * query.page_size
        hits = [
            SearchHit(entity=self._ref(entity_id), score=1.0, snippet=None)
            for entity_id in matched[start : start + query.page_size]
        ]
        return SearchPage(hits=hits, total=len(matched), page=query.page, page_size=query.page_size)

    def recent_entities(self, updated_since: str, entity_type: str | None, limit: int) -> list[EntityRef]:
        recent = self._updated_since(updated_since, entity_type)
        return [self._ref(self._ids[entity.key]) for entity in recent[:limit]]

    def count_recent_entities(self, updated_since: str, entity_type: str | None) -> int:
        return len(self._updated_since(updated_since, entity_type))

    def hydrate(self, entity_ids: Sequence[int]) -> dict[int, EntityRef]:
        return {eid: self._ref(eid) for eid in entity_ids if eid in self._entities}

    def iter_indexed_text(self) -> Iterator[tuple[int, str]]:
        return iter(
            [(entity_id, f"{title}\n\n{content}") for entity_id, (title, content, _tags) in sorted(self._text.items())]
        )

    # -- file catalog -----------------------------------------------------

    def upsert_catalog_entry(self, entry: CatalogEntry) -> int:
        existing = self._catalog.get(entry.original_path)
        entry_id = existing.entry_id if existing and existing.entry_id else self._next_catalog_id
        if entry_id == self._next_catalog_id:
            self._next_catalog_id += 1
        self._catalog[entry.original_path] = replace(entry, entry_id=entry_id)
        return entry_id

    def search_catalog(self, query: CatalogQuery) -> list[CatalogEntry]:
        return self._matching(query)[: query.limit]

    def count_catalog(self, query: CatalogQuery) -> int:
        return len(self._matching(query))

    def get_catalog_entry(self, original_path: str) -> CatalogEntry | None:
        return self._catalog.get(original_path)

    def get_catalog_entry_by_id(self, entry_id: int) -> CatalogEntry | None:
        return next((e for e in self._catalog.values() if e.entry_id == entry_id), None)

    def set_catalog_status(self, original_path: str, state: str, output_path: str | None, error: str | None) -> None:
        existing = self._catalog.get(original_path)
        if existing is None:
            return None
        self._catalog[original_path] = replace(existing, conversion_status=state, output_path=output_path, error=error)
        return None

    def remove_catalog_entry(self, original_path: str) -> bool:
        return self._catalog.pop(original_path, None) is not None

    def catalog_stats(self) -> dict[str, int]:
        stats = {state: 0 for state in self._config.catalog.conversion_states}
        for entry in self._catalog.values():
            if entry.conversion_status in stats:
                stats[entry.conversion_status] += 1
        return {"total": len(self._catalog), **stats}

    # -- internals --------------------------------------------------------

    def _ref(self, entity_id: int) -> EntityRef:
        entity = self._entities[entity_id]
        return EntityRef(
            entity_id=entity_id,
            key=entity.key,
            title=entity.title,
            entity_type=entity.entity_type,
            permalink=entity.permalink,
            file_path=entity.file_path,
            updated_at=entity.updated_at,
        )

    def _first(self, predicate: Callable[[Entity], bool]) -> EntityRef | None:
        for entity_id in sorted(self._entities):
            if predicate(self._entities[entity_id]):
                return self._ref(entity_id)
        return None

    def _updated_since(self, updated_since: str, entity_type: str | None) -> list[Entity]:
        """Newest first. The type filter applies here, before any limit does."""
        rows = [
            entity
            for entity in self._entities.values()
            if entity.updated_at >= updated_since and (entity_type is None or entity.entity_type == entity_type)
        ]
        return sorted(rows, key=lambda entity: entity.updated_at, reverse=True)

    def _matching(self, query: CatalogQuery) -> list[CatalogEntry]:
        rows = sorted(self._catalog.values(), key=lambda e: e.modified_at, reverse=True)
        return [row for row in rows if catalog_matches(row, query)]

    def _matches(self, entity_id: int, row: tuple[str, str, str], query: TextQuery) -> bool:
        entity = self._entities[entity_id]
        if query.fts is not None:
            haystack = " ".join(row).casefold()
            if not all(token in haystack for token in _search_tokens(query.fts)):
                return False
        if query.entity_type is not None and entity.entity_type != query.entity_type:
            return False
        if query.tag is not None and query.tag not in entity.tags:
            return False
        return all(self._metadata_matches(entity, f) for f in query.metadata)

    @staticmethod
    def _metadata_matches(entity: Entity, metadata_filter: MetadataFilter) -> bool:
        value: object = entity.metadata
        for part in metadata_filter.key.split("."):
            if not isinstance(value, dict):
                return False
            value = value.get(part)
        return evaluate(value, metadata_filter)
