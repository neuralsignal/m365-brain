"""Turning what a person typed into what a backend can execute.

Three parsers, no I/O, no SQL. The FTS normalizer and the metadata-filter parser
used to live inside a command-line script that assembled SQL strings directly,
which meant every new consumer either duplicated the parsing or duplicated the
SQL. Here they produce values -- an FTS5 expression and a `MetadataFilter` --
and each adapter renders them in its own dialect.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta

from m365_brain.index.backends.base import MetadataFilter
from m365_brain.index.fts_query import to_fts_query

# `to_fts_query` lives in `fts_query` -- it grew a parser of its own -- but it
# is re-exported here so callers keep one import for "turn user input into
# something a backend can execute".
__all__ = ["parse_metadata_filter", "parse_timeframe", "to_fts_query", "updated_since"]

# The shape the parser writes into `Entity.updated_at`. Every backend compares
# these lexicographically, so a cutoff that is not spelled identically -- an
# offset instead of `Z`, or a fractional second -- compares wrong rather than
# failing.
TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

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
