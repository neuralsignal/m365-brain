"""Three search paths behind one call: text, vector, and their fusion.

The vector path is the one with a real constraint. It recalls *chunks*, and a
chunk carries an entity id and nothing else, so the only filter it can honour
without a second round-trip is one that an `EntityRef` can answer -- the entity
type. A tag or metadata filter combined with `vector` or `hybrid` therefore
raises rather than being silently dropped: a filtered search that quietly
returns unfiltered results is worse than one that refuses.

`index.vector.enabled: false` also raises, naming the key. Falling back to text
search would answer a different question than the one asked and look like the
vector index simply had nothing to say.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

from m365_brain.config.index import IndexConfig
from m365_brain.index.backends.base import IndexBackend, MetadataFilter, TextQuery
from m365_brain.index.fusion import reciprocal_rank_fusion
from m365_brain.index.query import to_fts_query
from m365_brain.index.vector.base import EmbeddingProvider, VectorStore
from m365_brain.model import EntityRef, SearchHit, SearchPage

SearchMode = Literal["text", "vector", "hybrid"]


@dataclass(frozen=True, slots=True)
class SearchFilters:
    """What to narrow a search to, independent of how it is ranked."""

    entity_type: str | None
    tag: str | None
    metadata: tuple[MetadataFilter, ...]


@dataclass(frozen=True, slots=True)
class _SearchContext:
    """Validated query-execution state shared by the vector and hybrid paths."""

    config: IndexConfig
    backend: IndexBackend
    provider: EmbeddingProvider
    store: VectorStore
    text: str
    filters: SearchFilters


def search(
    config: IndexConfig,
    backend: IndexBackend,
    provider: EmbeddingProvider | None,
    store: VectorStore | None,
    text: str | None,
    mode: SearchMode,
    filters: SearchFilters,
    page: int,
    page_size: int,
) -> SearchPage:
    """One page of results, ranked by `mode`.

    `text` may be None only for `mode="text"`, which then lists everything the
    filters allow, newest first. Vector recall without a query has nothing to
    measure distance from.
    """
    if mode == "text":
        return backend.text_search(_text_query(text, filters, page, page_size))

    if text is None:
        raise ValueError(f"{mode} search requires query text; there is nothing to embed")
    provider, store = _require_vectors(config, provider, store, mode)
    _reject_unsupported_filters(filters, mode)

    ctx = _SearchContext(config, backend, provider, store, text, filters)
    if mode == "vector":
        return _vector_page(ctx, page, page_size)
    if mode == "hybrid":
        return _hybrid_page(ctx, page, page_size)
    raise ValueError(f"unknown search mode {mode!r}; expected text, vector or hybrid")


# -- text -----------------------------------------------------------------


def _text_query(text: str | None, filters: SearchFilters, page: int, page_size: int) -> TextQuery:
    return TextQuery(
        fts=to_fts_query(text) if text else None,
        entity_type=filters.entity_type,
        tag=filters.tag,
        metadata=filters.metadata,
        page=page,
        page_size=page_size,
    )


# -- vector ---------------------------------------------------------------


def _vector_page(ctx: _SearchContext, page: int, page_size: int) -> SearchPage:
    ranked = _vector_candidates(ctx)
    start = (page - 1) * page_size
    hits = [
        SearchHit(entity=entity, score=_similarity(distance), snippet=None)
        for entity, distance in ranked[start : start + page_size]
    ]
    return SearchPage(hits=hits, total=len(ranked), page=page, page_size=page_size)


def _vector_candidates(ctx: _SearchContext) -> list[tuple[EntityRef, float]]:
    """Nearest chunks, thresholded, collapsed to one row per entity.

    A long document contributes many chunks and would otherwise fill the page
    with itself; its best chunk is the one that says how well it matches.
    """
    floor = ctx.config.search.min_similarity
    best: dict[int, float] = {}
    for hit in ctx.store.query(ctx.provider.embed_query(ctx.text), ctx.config.search.vector_candidates):
        if _similarity(hit.distance) < floor:
            continue
        if hit.entity_id not in best or hit.distance < best[hit.entity_id]:
            best[hit.entity_id] = hit.distance

    entities = ctx.backend.hydrate(sorted(best))
    ranked = [
        (entities[entity_id], distance)
        for entity_id, distance in best.items()
        if entity_id in entities and _passes(entities[entity_id], ctx.filters)
    ]
    ranked.sort(key=lambda pair: (pair[1], pair[0].entity_id))
    return ranked


# -- hybrid ---------------------------------------------------------------


def _hybrid_page(ctx: _SearchContext, page: int, page_size: int) -> SearchPage:
    depth = ctx.config.search.vector_candidates
    text_page = ctx.backend.text_search(_text_query(ctx.text, ctx.filters, page=1, page_size=depth))
    text_ranked = [(hit.entity.entity_id, hit.score) for hit in text_page.hits]
    vector_ranked = [(entity.entity_id, distance) for entity, distance in _vector_candidates(ctx)]

    fused = reciprocal_rank_fusion(
        text_ranked, vector_ranked, ctx.config.search.rrf_k, ctx.config.search.rrf_min_weight
    )
    known: dict[int, SearchHit] = {hit.entity.entity_id: hit for hit in text_page.hits}
    hydrated = ctx.backend.hydrate([entity_id for entity_id, _score in fused if entity_id not in known])

    start = (page - 1) * page_size
    hits = [
        _fused_hit(entity_id, score, known, hydrated)
        for entity_id, score in fused[start : start + page_size]
        if entity_id in known or entity_id in hydrated
    ]
    return SearchPage(hits=hits, total=len(fused), page=page, page_size=page_size)


def _fused_hit(entity_id: int, score: float, known: dict[int, SearchHit], hydrated: dict[int, EntityRef]) -> SearchHit:
    """Keep the text path's snippet when it produced one; the vector path has none."""
    if entity_id in known:
        return replace(known[entity_id], score=score)
    return SearchHit(entity=hydrated[entity_id], score=score, snippet=None)


# -- guards ---------------------------------------------------------------


def _require_vectors(
    config: IndexConfig, provider: EmbeddingProvider | None, store: VectorStore | None, mode: SearchMode
) -> tuple[EmbeddingProvider, VectorStore]:
    if not config.vector.enabled:
        raise ValueError(f"{mode} search needs index.vector.enabled: true, and it is false")
    if provider is None or store is None:
        raise ValueError(
            f"{mode} search needs an embedding provider and a vector store; "
            f"index.vector.enabled is true but neither was supplied"
        )
    return provider, store


def _reject_unsupported_filters(filters: SearchFilters, mode: SearchMode) -> None:
    unsupported = [name for name, value in (("tag", filters.tag), ("metadata", filters.metadata or None)) if value]
    if unsupported:
        raise ValueError(
            f"{mode} search cannot apply {unsupported} filters: vector recall returns chunks, "
            f"which carry no tags or metadata. Filter by entity_type, or search with mode='text'."
        )


def _passes(entity: EntityRef, filters: SearchFilters) -> bool:
    return filters.entity_type is None or entity.entity_type == filters.entity_type


def _similarity(distance: float) -> float:
    """Distance folded into `(0, 1]` -- the number `index.search.min_similarity` is in."""
    return 1.0 / (1.0 + max(distance, 0.0))


__all__ = ["SearchFilters", "SearchMode", "search"]
