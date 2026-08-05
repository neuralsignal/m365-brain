"""Reciprocal rank fusion, as properties rather than as expected numbers.

Fused scores are arbitrary in magnitude -- the only thing that means anything is
the order they induce. Asserting a specific score would pin the arithmetic;
asserting the ordering properties pins the behaviour, which is what a caller
actually depends on.
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from m365_brain.index.fusion import reciprocal_rank_fusion

K = 60
MIN_WEIGHT = 0.1


def _candidates(descending: bool):
    """Both inputs are documented "best first", and the generator honours it.

    For the text list best means the largest absolute BM25 score; for the vector
    list it means the smallest distance. A generator that emitted arbitrary
    orders would be testing the function against inputs no backend produces.
    """
    return st.lists(
        st.tuples(st.integers(min_value=1, max_value=30), st.floats(min_value=0.0, max_value=50.0)),
        max_size=12,
    ).map(
        lambda pairs: sorted(
            {entity_id: score for entity_id, score in pairs}.items(),
            key=lambda pair: pair[1],
            reverse=descending,
        )
    )


text_lists = _candidates(descending=True)
vector_lists = _candidates(descending=False)


def fuse(text_ranked, vector_ranked):
    return reciprocal_rank_fusion(text_ranked, vector_ranked, K, MIN_WEIGHT)


@given(text_lists, vector_lists)
def test_the_result_is_the_union_without_duplicates(text_ranked, vector_ranked):
    fused = fuse(text_ranked, vector_ranked)
    ids = [entity_id for entity_id, _score in fused]
    expected = {entity_id for entity_id, _score in text_ranked} | {entity_id for entity_id, _score in vector_ranked}
    assert set(ids) == expected
    assert len(ids) == len(set(ids))


@given(text_lists, vector_lists)
def test_scores_descend(text_ranked, vector_ranked):
    scores = [score for _entity_id, score in fuse(text_ranked, vector_ranked)]
    assert scores == sorted(scores, reverse=True)


@given(text_lists, vector_lists)
def test_the_order_is_total(text_ranked, vector_ranked):
    """Equal scores break on entity id, so two runs cannot disagree."""
    assert fuse(text_ranked, vector_ranked) == fuse(text_ranked, vector_ranked)


@given(text_lists)
def test_an_empty_second_list_preserves_the_first_order(text_ranked):
    fused = fuse(text_ranked, [])
    assert [entity_id for entity_id, _score in fused] == [entity_id for entity_id, _score in text_ranked]


@given(vector_lists)
def test_an_empty_first_list_preserves_the_second_order(vector_ranked):
    fused = fuse([], vector_ranked)
    assert [entity_id for entity_id, _score in fused] == [entity_id for entity_id, _score in vector_ranked]


def test_both_lists_empty_gives_nothing():
    assert fuse([], []) == []


def test_agreement_beats_a_single_strong_showing():
    """The whole point: appearing on both lists outranks topping one of them."""
    fused = dict(fuse([(1, 10.0), (2, 9.0)], [(2, 0.1), (3, 0.2)]))
    assert fused[2] > fused[1]
    assert fused[2] > fused[3]


def test_rank_matters_more_than_score_magnitude():
    """Ranks have no units; BM25 scores and distances are not comparable quantities."""
    small = fuse([(1, 0.001), (2, 0.0005)], [])
    large = fuse([(1, 1000.0), (2, 500.0)], [])
    assert [entity_id for entity_id, _score in small] == [entity_id for entity_id, _score in large]


def test_a_list_of_zero_scores_still_contributes():
    """The weight floor is why: zero relevance on one list is still a ranking on it."""
    fused = dict(fuse([(1, 0.0), (2, 0.0)], []))
    assert fused[1] > fused[2] > 0.0


def test_a_larger_k_flattens_the_head():
    """`k` damps rank 1 so agreement further down can still win."""
    sharp = reciprocal_rank_fusion([(1, 1.0)], [], k=1, min_weight=MIN_WEIGHT)
    flat = reciprocal_rank_fusion([(1, 1.0)], [], k=1000, min_weight=MIN_WEIGHT)
    assert sharp[0][1] > flat[0][1]


def test_the_weight_floor_raises_a_weak_contribution():
    weak = reciprocal_rank_fusion([(1, 10.0), (2, 0.0001)], [], k=K, min_weight=0.0)
    floored = reciprocal_rank_fusion([(1, 10.0), (2, 0.0001)], [], k=K, min_weight=0.5)
    assert dict(floored)[2] > dict(weak)[2]
