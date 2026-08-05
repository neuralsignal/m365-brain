"""Turning what a person typed into what a backend can execute.

Three parsers, no I/O, no SQL. The FTS normalizer and the metadata-filter parser
used to live inside a command-line script that assembled SQL strings directly,
which meant every new consumer either duplicated the parsing or duplicated the
SQL. Here they produce values -- an FTS5 expression and a `MetadataFilter` --
and each adapter renders them in its own dialect.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timedelta

from m365_brain.index.backends.base import MetadataFilter

# The shape the parser writes into `Entity.updated_at`. Every backend compares
# these lexicographically, so a cutoff that is not spelled identically -- an
# offset instead of `Z`, or a fractional second -- compares wrong rather than
# failing.
TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

_OPERATORS = frozenset({"AND", "OR", "NOT"})
_BREAKS = '()"'

# key, operator, value. `~` is set membership, `:` is a `lo..hi` range.
_FIELD_RE = re.compile(r"^([\w.]+)(>=|<=|>|<|~|:|=)(.+)$")
_OPS: dict[str, str] = {"=": "eq", ">": "gt", ">=": "gte", "<": "lt", "<=": "lte", "~": "in", ":": "between"}

_COMPACT_TIMEFRAME_RE = re.compile(r"^(\d+)\s*([dhwm])$")
_SPELLED_TIMEFRAME_RE = re.compile(r"^(\d+)\s+(day|days|week|weeks|month|months)\s+ago$")

# A month is 30 days here. Calendar months are not a duration, and "6m ago" as
# a search filter does not want the calendar's answer.
_NAMED_TIMEFRAMES: dict[str, timedelta] = {
    "today": timedelta(days=0),
    "yesterday": timedelta(days=1),
    "last week": timedelta(weeks=1),
    "last month": timedelta(days=30),
}
_UNITS: dict[str, timedelta] = {
    "d": timedelta(days=1),
    "h": timedelta(hours=1),
    "w": timedelta(weeks=1),
    "m": timedelta(days=30),
}


def to_fts_query(text: str) -> str:
    """Normalize free text into an FTS5 expression.

    Terms are ANDed, not ORed: someone typing two words wants documents with
    both, and OR-by-default makes the second word dilute the first. Bare terms
    gain a `*` so a search for `proj` finds `project`; quoted phrases and
    parentheses pass through untouched, and a leading `-` becomes `NOT`.

    `and`, `or` and `not` are read as operators whatever their case. That does
    mean a sentence containing the word "and" loses it, which is the price of
    not requiring anyone to shout; a phrase in quotes keeps every word.
    """
    text = _without_control_characters(text).strip()
    if not text:
        return ""
    tokens = _balanced(_tokenize(text))
    if not tokens:
        return ""
    tokens = _with_implicit_and(tokens)
    _reject_unary_not(tokens, text)
    return " ".join(tokens)


def _without_control_characters(text: str) -> str:
    """Drop C0/C1 control characters, keeping ordinary whitespace.

    A NUL is the one that matters: SQLite reads it as a string terminator and
    reports "unterminated string" for an expression that looks fine in Python.
    None of these carry search meaning, so dropping beats rejecting -- text
    pasted out of a PDF or a terminal routinely carries a stray one.
    """
    return "".join(
        character for character in text if character.isspace() or not unicodedata.category(character).startswith("C")
    )


def _balanced(tokens: list[str]) -> list[str]:
    """Drop unmatched `)`, close unmatched `(`, and remove empty groups.

    A stray bracket is a syntax error in FTS5, so a query that is nothing but
    `)` used to abort the search. Typing an unbalanced bracket is an ordinary
    slip, and the useful reading of it is "they meant the terms", not "fail".
    Empty groups go too: `( )` is a syntax error in its own right, and an
    unbalanced bracket often leaves one behind once the partner is dropped.
    """
    kept: list[str] = []
    depth = 0
    for token in tokens:
        if token == ")":
            if depth == 0:
                continue  # nothing open -- the bracket is noise
            depth -= 1
        elif token == "(":
            depth += 1
        kept.append(token)
    kept.extend(")" * depth)

    while True:
        for position in range(len(kept) - 1):
            if kept[position] == "(" and kept[position + 1] == ")":
                del kept[position : position + 2]
                break
        else:
            return kept


def _reject_unary_not(tokens: list[str], text: str) -> None:
    """FTS5's NOT is binary; every one of them needs a left operand.

    `NOT java*`, `a* OR NOT b*` and `( NOT b* )` are all syntax errors rather
    than "everything except b" -- FTS5 simply cannot express a complemented
    set. Saying so here beats letting the driver report a syntax error against
    a query string the user never typed and cannot read.

    `AND NOT` does not reach this check: it means the same as `NOT` and is
    rewritten on the way in.
    """
    for position, token in enumerate(tokens):
        if token != "NOT":
            continue
        previous = tokens[position - 1] if position else None
        if previous is None or previous in ("AND", "OR", "NOT", "("):
            raise ValueError(
                f"negation needs something to subtract from: {text!r} "
                f"(FTS5 has no unary NOT -- try `something {text.strip()}`)"
            )


def parse_metadata_filter(expression: str) -> MetadataFilter:
    """Parse `key=v`, `key>=3`, `key~a,b,c` or `key:lo..hi` into a filter.

    Dotted keys address nested values; the adapter turns them into whatever its
    store's path syntax is.
    """
    match = _FIELD_RE.match(expression)
    if not match:
        raise ValueError(
            f"cannot parse metadata filter {expression!r}; expected key=value, key>=n, key~a,b or key:lo..hi"
        )
    key, symbol, raw = match.group(1), match.group(2), match.group(3)
    op = _OPS[symbol]

    if op == "in":
        return MetadataFilter(key=key, op=op, values=tuple(value.strip() for value in raw.split(",")))
    if op == "between":
        if ".." not in raw:
            raise ValueError(f"range filter {expression!r} needs lo..hi")
        low, high = raw.split("..", 1)
        return MetadataFilter(key=key, op=op, values=(float(low), float(high)))
    if op == "eq":
        return MetadataFilter(key=key, op=op, values=(raw,))
    # The ordering operators are numeric: a text `>=` on frontmatter scalars
    # compares digit strings, where "10" sorts below "3".
    return MetadataFilter(key=key, op=op, values=(float(raw),))


def parse_timeframe(text: str) -> timedelta:
    """Parse `7d`, `3 weeks ago`, `last month` and friends into a duration.

    Unparseable input raises: silently returning a week would make a filter
    nobody asked for look like it worked.
    """
    text = text.strip().lower()

    named = _NAMED_TIMEFRAMES.get(text)
    if named is not None:
        return named

    compact = _COMPACT_TIMEFRAME_RE.match(text)
    if compact:
        return int(compact.group(1)) * _UNITS[compact.group(2)]

    spelled = _SPELLED_TIMEFRAME_RE.match(text)
    if spelled:
        return int(spelled.group(1)) * _UNITS[spelled.group(2)[0]]

    raise ValueError(f"cannot parse timeframe {text!r}; try '7d', '3 weeks ago', 'today' or 'last month'")


def updated_since(timeframe: str, now: datetime) -> str:
    """The cutoff timestamp `recent_entities` filters on.

    `now` is a parameter so the only clock in the call chain belongs to the
    caller, which is what makes this testable without freezing time globally.
    """
    return (now - parse_timeframe(timeframe)).strftime(TIMESTAMP_FORMAT)


# -- internals ------------------------------------------------------------


def _tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    position = 0
    while position < len(text):
        character = text[position]
        if character == '"':
            # An unterminated quote is closed rather than passed through. FTS5
            # rejects an odd quote count outright, so leaving it open turns a
            # half-typed phrase into a crash instead of a search.
            end = text.find('"', position + 1)
            if end == -1:
                tokens.append(text[position:] + '"')
                position = len(text)
            else:
                tokens.append(text[position : end + 1])
                position = end + 1
        elif character in "()":
            tokens.append(character)
            position += 1
        elif character.isspace():
            position += 1
        elif character == "-":
            # A hyphen only starts a token after whitespace, an operator or a
            # bracket -- inside a word it is consumed by `_take_word`. So a
            # leading one is always a negation, including after a plain term:
            # `python -java` is the ordinary way people write it, and the
            # version that only negated after AND/OR/( turned it into a bare
            # `-java*`, which the tokenizer then reads as `java*` -- the exact
            # opposite of what was asked, silently.
            position = _take_negated_word(text, position + 1, tokens)
        else:
            position = _take_word(text, position, tokens)
    return tokens


def _take_negated_word(text: str, start: int, tokens: list[str]) -> int:
    """Emit `NOT word*`, absorbing a preceding `AND`.

    FTS5's `NOT` is a *binary* operator, so `a AND NOT b` is a syntax error
    where `a NOT b` is the same intent spelled correctly. A user writing
    `python AND -java` means the latter, so the explicit `AND` is dropped
    rather than passed through to fail.
    """
    end = _word_end(text, start)
    if end > start:
        if tokens and tokens[-1] == "AND":
            tokens.pop()
        tokens.append("NOT")
        tokens.append(_as_term(text[start:end]))
    return end


def _as_term(word: str) -> str:
    """Render one word as an FTS5 prefix term, quoting it if it needs quoting.

    An FTS5 bareword is alphanumerics and underscores. Anything else -- the
    hyphen in `well-known`, the dot and at-sign in an email address -- makes
    the parser read the rest as a column filter and fail with "no such column",
    aborting the whole search. Quoting turns the word into a phrase, which
    tokenizes to the same terms and still accepts a trailing `*`.
    """
    if word.replace("_", "").isalnum():
        return word + "*"
    return f'"{word}"*'


def _take_word(text: str, start: int, tokens: list[str]) -> int:
    end = _word_end(text, start)
    word = text[start:end]
    tokens.append(word.upper() if word.upper() in _OPERATORS else _as_term(word))
    return end


def _word_end(text: str, start: int) -> int:
    end = start
    while end < len(text) and not text[end].isspace() and text[end] not in _BREAKS:
        end += 1
    return end


def _with_implicit_and(tokens: list[str]) -> list[str]:
    """Insert `AND` between two adjacent terms that carry no operator."""
    result: list[str] = []
    for token in tokens:
        if result and token not in ("AND", "OR", "NOT", ")") and result[-1] not in ("AND", "OR", "NOT", "("):
            result.append("AND")
        result.append(token)
    return result
