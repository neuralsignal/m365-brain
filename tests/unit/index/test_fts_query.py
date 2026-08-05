"""`to_fts_query` must emit an expression a real FTS5 parser accepts.

That is the property; every other assertion here is a proxy for it. Five
defects reached SQLite and aborted a search before it was pinned -- a hyphen
read as a column filter, a negation the tokenizer inverted, an unterminated
quote, an unbalanced bracket, and a NUL byte -- and balanced-quote and
star-count assertions passed through every one of them.
"""

from __future__ import annotations

import contextlib
import sqlite3

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from m365_brain.index.fts_query import to_fts_query

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


@pytest.mark.parametrize(
    ("typed", "expected"),
    [
        ("OR", ""),
        ("or", ""),
        ("AND", ""),
        ("cats OR", "cats*"),
        ("OR cats", "cats*"),
        ("a AND", "a*"),
        ("cats OR OR dogs", "cats* OR dogs*"),
    ],
)
def test_a_dangling_conjunction_is_dropped(typed, expected):
    """FTS5 rejects an operator missing an operand -- each of these aborted the search.

    A trailing `OR` is a slip, and the useful reading is the terms around it.
    `NOT` is deliberately not in this list: dropping a negation would return
    the *opposite* result set rather than a wider one, so it raises instead.
    """
    assert to_fts_query(typed) == expected


def test_a_dangling_negation_still_raises_rather_than_being_dropped():
    with pytest.raises(ValueError, match="negation"):
        to_fts_query("cats OR -dogs")
