"""Metadata operator semantics -- the shared definition both backends obey."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from m365_brain.index.backends.base import MetadataFilter
from m365_brain.index.backends.filters import SQL_COMPARISON, evaluate


def a_filter(op: str, *values: str | float) -> MetadataFilter:
    return MetadataFilter(key="k", op=op, values=values)


@pytest.mark.parametrize(
    ("op", "values", "value", "expected"),
    [
        ("eq", ("open",), "open", True),
        ("eq", ("open",), "closed", False),
        ("gt", (3.0,), "4", True),
        ("gt", (3.0,), "3", False),
        ("gte", (3.0,), "3", True),
        ("lt", (3.0,), "2", True),
        ("lte", (3.0,), "3", True),
        ("in", ("a", "b"), "b", True),
        ("in", ("a", "b"), "c", False),
        ("between", (1.0, 5.0), "3", True),
        ("between", (1.0, 5.0), "9", False),
    ],
)
def test_operators(op, values, value, expected):
    assert evaluate(value, a_filter(op, *values)) is expected


def test_missing_value_never_matches():
    for op in ("eq", "gt", "gte", "lt", "lte", "in", "between"):
        values = (1.0, 5.0) if op == "between" else (1.0,)
        assert evaluate(None, a_filter(op, *values)) is False


def test_numeric_comparison_is_not_lexicographic():
    """`"10" < "3"` as text; the whole point of the coercion is that it is not."""
    assert evaluate("10", a_filter("gt", 3.0)) is True
    assert evaluate("3", a_filter("gt", 10.0)) is False


def test_non_numeric_text_compares_as_text():
    assert evaluate("beta", a_filter("gt", "alpha")) is True
    assert evaluate("alpha", a_filter("gt", "beta")) is False


def test_booleans_compare_as_text_never_as_one():
    """Frontmatter normalises `true` to `"True"`; a raw bool is treated the same.

    Without the guard, `float(True) == 1.0` would make `flag == 1` true for
    every set flag and silently equate two unrelated vocabularies.
    """
    assert evaluate(True, a_filter("eq", "True")) is True
    assert evaluate(True, a_filter("eq", 1.0)) is False


def test_sql_comparison_covers_the_scalar_operators():
    assert set(SQL_COMPARISON) == {"eq", "gt", "gte", "lt", "lte"}


@given(st.floats(allow_nan=False, allow_infinity=False, width=32))
def test_equality_is_reflexive_for_numbers(number):
    assert evaluate(str(number), a_filter("eq", number)) is True
