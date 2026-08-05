"""Query normalization: FTS syntax, metadata filters, timeframes.

The FTS properties matter more than the table cases. Whatever a person types
reaches SQLite's FTS5 parser, and an expression that parser rejects is a crash
in the middle of a search rather than an empty result -- so the properties say
"never raises" and "never emits an unbalanced quote" about arbitrary text, not
about the inputs somebody thought of.
"""

from __future__ import annotations

import contextlib
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from m365_brain.index.query import parse_metadata_filter, parse_timeframe, to_fts_query, updated_since

# -- to_fts_query ---------------------------------------------------------


def _fts_accepts(expression: str) -> None:
    """Run the expression through a real FTS5 parser. Raises if it rejects it."""
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE VIRTUAL TABLE probe USING fts5(body)")
    connection.execute("SELECT count(*) FROM probe WHERE probe MATCH ?", (expression,))


@given(st.text())
def test_never_raises_except_on_a_bare_negation(text):
    try:
        to_fts_query(text)
    except ValueError as error:
        assert "negation" in str(error)


@given(st.text())
def test_output_is_valid_fts5(text):
    """The property that matters: whatever comes out, FTS5 must accept it.

    Everything else here is a proxy for this. Four separate defects -- an
    unterminated quote, a mid-query `-`, a unary NOT, and a hyphen inside a
    bareword -- all reached the FTS5 parser and aborted the search with an
    error nobody could act on. Balanced quotes and star counts are symptoms;
    "the parser takes it" is the actual contract.
    """
    try:
        expression = to_fts_query(text)
    except ValueError:
        return  # rejected deliberately, with a message -- that is the good case
    if expression:
        _fts_accepts(expression)


@given(st.text())
def test_quotes_stay_balanced(text):
    """An odd quote count is a parse error inside FTS5, not an empty result."""
    with contextlib.suppress(ValueError):  # a deliberate rejection is the good case
        assert to_fts_query(text).count('"') % 2 == 0


@given(st.text(alphabet=st.characters(min_codepoint=97, max_codepoint=122), min_size=1, max_size=12))
def test_a_bare_word_gains_exactly_one_star(word):
    assume(word.upper() not in {"AND", "OR", "NOT"})
    assert to_fts_query(word) == f"{word}*"


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ("", ""),
        ("   ", ""),
        ("python", "python*"),
        ("python django", "python* AND django*"),
        ("python OR java", "python* OR java*"),
        ("python NOT java", "python* NOT java*"),
        ("python and java", "python* AND java*"),
        ("python -java", "python* NOT java*"),
        ("python AND -java", "python* NOT java*"),
        ("well-known", '"well-known"*'),
        ("foo@bar.com", '"foo@bar.com"*'),
        ('"exact phrase"', '"exact phrase"'),
        ('"exact phrase" python', '"exact phrase" AND python*'),
        ("(python OR java) web", "( python* OR java* ) AND web*"),
    ],
)
def test_table_of_syntax(typed, expected):
    assert to_fts_query(typed) == expected


def test_operators_are_recognised_in_any_case():
    """Nobody should have to shout to get a boolean search."""
    assert to_fts_query("cats and dogs") == to_fts_query("cats AND dogs")


def test_a_quoted_operator_stays_a_word():
    assert to_fts_query('"cats and dogs"') == '"cats and dogs"'


def test_an_unterminated_quote_is_closed():
    """FTS5 rejects an odd quote count, so a half-typed phrase would crash the search."""
    assert to_fts_query('"half a phrase') == '"half a phrase"'


def test_negation_works_after_a_plain_term():
    """`python -java` is how people write it; a bare `-java*` reads as `java*`."""
    assert "NOT java*" in to_fts_query("python -java")


# -- parse_metadata_filter ------------------------------------------------


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


# -- parse_timeframe ------------------------------------------------------


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


@pytest.mark.parametrize("typed", ["-java", "a OR -b", "a (-b)"])
def test_a_negation_with_no_left_operand_is_rejected(typed):
    """FTS5's NOT is binary -- it cannot express a complemented set.

    Failing here with a sentence beats letting the driver report a syntax
    error against a generated string the user never typed and cannot read.
    """
    with pytest.raises(ValueError, match="negation"):
        to_fts_query(typed)


@pytest.mark.parametrize(
    "typed",
    ["well-known thing", "foo@bar.com", "python -java", "well-known -java", '"half a phrase', "a.b.c"],
)
def test_real_fts5_accepts_the_output(typed):
    _fts_accepts(to_fts_query(typed))


@pytest.mark.parametrize("typed", [")", "((", "( )", "a )", "( a", "\x00", "a\x00b"])
def test_malformed_input_still_produces_valid_fts5(typed):
    """Stray brackets and control characters are slips, not queries.

    Each of these used to reach the FTS5 parser and abort the search: an
    unmatched bracket is a syntax error, and a NUL is read by SQLite as a
    string terminator ("unterminated string" on an expression that looks fine
    in Python). The useful reading of a slip is the terms around it.
    """
    expression = to_fts_query(typed)
    if expression:
        _fts_accepts(expression)
