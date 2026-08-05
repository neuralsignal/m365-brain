"""Chunking properties and the chunk-key round trip.

The size limit is a target, not a guarantee: a single line longer than the limit
is emitted whole, because breaking mid-token to satisfy a number costs more than
the oversized chunk does. The properties below say exactly that, so nobody
later "fixes" it into a hard cut.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from m365_brain.index.vector.chunking import chunk_index, chunk_key_for, split_into_chunks

SIZE = 60
OVERLAP = 10


@given(st.text())
def test_never_raises(text):
    split_into_chunks(text, SIZE, OVERLAP)


@given(st.text())
def test_blank_input_yields_nothing(text):
    """A chunk of whitespace is a nearest neighbour to everything."""
    if not text.strip():
        assert split_into_chunks(text, SIZE, OVERLAP) == []


@given(st.text(min_size=1))
def test_non_blank_input_yields_at_least_one_chunk(text):
    if text.strip():
        assert split_into_chunks(text, SIZE, OVERLAP) != []


@given(st.text())
def test_no_chunk_is_blank(text):
    assert all(chunk.strip() for chunk in split_into_chunks(text, SIZE, OVERLAP))


@given(st.lists(st.text(alphabet=st.characters(min_codepoint=97, max_codepoint=122), min_size=1, max_size=20)))
def test_only_an_unbreakable_line_may_exceed_the_limit(words):
    text = " ".join(words)
    for chunk in split_into_chunks(text, SIZE, OVERLAP):
        assert len(chunk) <= SIZE or "\n" not in chunk


def test_a_short_document_is_one_chunk():
    assert split_into_chunks("# Title\n\nA short body.", SIZE, OVERLAP) == ["Title\n\nA short body."]


def test_a_long_document_is_split():
    text = "\n".join(f"line {n} " + "x" * 40 for n in range(20))
    chunks = split_into_chunks(text, SIZE, OVERLAP)
    assert len(chunks) > 1


def test_overlap_carries_text_forward():
    """A sentence straddling a cut stays retrievable from the chunk after it."""
    text = "\n".join("y" * 50 for _ in range(4))
    chunks = split_into_chunks(text, SIZE, OVERLAP)
    assert len(chunks) > 1
    assert chunks[1].startswith("y" * OVERLAP)


def test_headers_are_split_points():
    text = "# One\n\n" + "a" * 55 + "\n\n# Two\n\n" + "b" * 55
    chunks = split_into_chunks(text, SIZE, OVERLAP)
    assert any(chunk.startswith("One") for chunk in chunks)
    assert any(chunk.startswith("Two") for chunk in chunks)


@given(st.integers(min_value=0, max_value=10_000))
def test_chunk_keys_round_trip(index):
    assert chunk_index(chunk_key_for(index)) == index


def test_a_key_without_the_prefix_raises():
    with pytest.raises(ValueError, match="not a chunk key"):
        chunk_index("piece_3")


def test_two_digit_keys_order_numerically():
    """Text ordering puts `chunk_10` before `chunk_9`; this is why the inverse exists."""
    keys = [chunk_key_for(n) for n in range(12)]
    assert sorted(keys, key=chunk_index) == keys
    assert sorted(keys) != keys
