"""Reciprocal rank fusion. Pure arithmetic over two ranked lists.

Rank fusion rather than score fusion because BM25 and vector distance are not
comparable quantities: one is an unbounded relevance score, the other a
geometric distance, and normalizing them onto a shared scale means inventing an
exchange rate that changes with every corpus. Ranks have no units.

The two tuning numbers arrive as arguments, not as a config object, so the
function is directly property-testable and the caller stays the only place that
reads config.
"""

from __future__ import annotations

from collections.abc import Sequence


def reciprocal_rank_fusion(
    text_ranked: Sequence[tuple[int, float]],
    vector_ranked: Sequence[tuple[int, float]],
    k: int,
    min_weight: float,
) -> list[tuple[int, float]]:
    """Fuse `(entity id, score)` lists, best first, into one ranking.

    `text_ranked` carries absolute BM25 scores; `vector_ranked` carries
    distances. Each list contributes `weight / (k + rank)` per entity, and the
    contributions sum -- which is what makes agreement between the two lists
    outrank a strong showing in either alone.

    `k` damps the head of each list: without it the top result would dominate
    any amount of agreement further down. `min_weight` floors each contribution
    so a document that ranks well on a list where every score is tiny still
    counts as ranking well on that list.
    """
    scores: dict[int, float] = {}
    for ranked, weights in (
        (text_ranked, _normalized_weights([score for _entity_id, score in text_ranked], min_weight)),
        (vector_ranked, _similarity_weights([score for _entity_id, score in vector_ranked], min_weight)),
    ):
        for rank, ((entity_id, _score), weight) in enumerate(zip(ranked, weights, strict=True), start=1):
            scores[entity_id] = scores.get(entity_id, 0.0) + weight / (k + rank)

    # Ties break on entity id so the order is total: two documents with equal
    # fused scores must not swap places between runs.
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))


def _normalized_weights(scores: Sequence[float], min_weight: float) -> list[float]:
    """Scores scaled by the list maximum, floored -- relative standing within one list."""
    largest = max((abs(score) for score in scores), default=0.0)
    if largest <= 0.0:
        return [min_weight] * len(scores)
    return [max(abs(score) / largest, min_weight) for score in scores]


def _similarity_weights(distances: Sequence[float], min_weight: float) -> list[float]:
    """`1 / (1 + distance)`, floored. Already bounded in `(0, 1]`, so no rescaling."""
    return [max(1.0 / (1.0 + max(distance, 0.0)), min_weight) for distance in distances]
