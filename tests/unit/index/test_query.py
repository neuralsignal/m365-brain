"""Query normalization: FTS syntax, metadata filters, timeframes.

The FTS properties matter more than the table cases. Whatever a person types
reaches SQLite's FTS5 parser, and an expression that parser rejects is a crash
in the middle of a search rather than an empty result -- so the properties say
"never raises" and "never emits an unbalanced quote" about arbitrary text, not
about the inputs somebody thought of.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from m365_brain.index.query import parse_metadata_filter, parse_timeframe, updated_since

# -- to_fts_query ---------------------------------------------------------


@pytest.mark.parametrize(
    ("expression", "key", "op", "values"),
    [
        ("status=active", "status", "eq", ("active",)),
        ("priority>2", "priority", "gt", (2.0,)),
        ("priority>=2", "priority", "gte", (2.0,)),
        ("priority<2", "priority", "lt", (2.0,)),
        ("priority<=2", "priority", "lte", (2.0,)),
        ("status~open,closed", "status", "in", ("open", "closed")),
        ("score:0.5..1.0", "score", "between", (0.5, 1.0)),
        ("schema.confidence>=0.9", "schema.confidence", "gte", (0.9,)),
    ],
)
def test_operators_parse(expression, key, op, values):
    parsed = parse_metadata_filter(expression)
    assert (parsed.key, parsed.op, parsed.values) == (key, op, values)


def test_membership_values_are_trimmed():
    assert parse_metadata_filter("status~open, closed").values == ("open", "closed")


def test_ordering_operators_carry_numbers_not_text():
    """`"10" < "3"` as text; a numeric filter that compares as text is silently wrong."""
    assert all(isinstance(value, float) for value in parse_metadata_filter("priority>=10").values)


@pytest.mark.parametrize("expression", ["nonsense", "key!value", "score:0.5", "=value", "priority>abc"])
def test_unparseable_expressions_raise(expression):
    with pytest.raises(ValueError):
        parse_metadata_filter(expression)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("today", timedelta(days=0)),
        ("yesterday", timedelta(days=1)),
        ("last week", timedelta(weeks=1)),
        ("last month", timedelta(days=30)),
        ("7d", timedelta(days=7)),
        ("3h", timedelta(hours=3)),
        ("2w", timedelta(weeks=2)),
        ("6m", timedelta(days=180)),
        ("7 days ago", timedelta(days=7)),
        ("1 day ago", timedelta(days=1)),
        ("3 weeks ago", timedelta(weeks=3)),
        ("2 months ago", timedelta(days=60)),
        ("  LAST WEEK  ", timedelta(weeks=1)),
    ],
)
def test_timeframes_parse(text, expected):
    assert parse_timeframe(text) == expected


@pytest.mark.parametrize("text", ["", "soon", "5 fortnights ago", "d7", "-3d"])
def test_unparseable_timeframes_raise(text):
    with pytest.raises(ValueError, match="cannot parse timeframe"):
        parse_timeframe(text)


def test_updated_since_is_measured_from_the_clock_it_is_given():
    now = datetime(2026, 3, 15, 12, 0, 0, tzinfo=UTC)
    assert updated_since("7d", now) == "2026-03-08T12:00:00Z"


def test_updated_since_matches_the_stored_timestamp_shape():
    """Backends compare these lexicographically, so the shape is the contract."""
    stamp = updated_since("today", datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC))
    assert stamp == "2026-01-02T03:04:05Z"
