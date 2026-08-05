"""One definition of what a `MetadataFilter` operator means.

The in-memory backend evaluates filters in Python; the SQLite backend renders
them as SQL. Two independent readings of "gte" is a defect waiting for the day
they disagree, so the semantics live here: `evaluate` for the former,
`SQL_COMPARISON` for the latter.

Frontmatter scalars are normalised to strings when they are parsed, so
`priority: 3` is stored as `"3"`. A numeric filter therefore has to coerce
before comparing -- otherwise `priority>=10` is false because `"10" < "3"` as
text.

`normalised_extension` is here for the same reason and after the same defect:
the two backends each normalised a catalog `--ext` filter their own way, one
case-sensitively and one not, so the same query returned different rows
depending on configuration. It was invisible while the catalog was empty.
"""

from __future__ import annotations

from m365_brain.index.backends.base import MetadataFilter

# op -> SQL comparison operator, for the operators that map to one.
SQL_COMPARISON: dict[str, str] = {"eq": "=", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}


def normalised_extension(extension: str) -> str:
    """`PDF`, `.PDF` and `.pdf` are one filter: a lower-cased, dotted suffix.

    Registration stores the lower-cased suffix, so a filter that does not
    lower-case matches nothing -- and `--ext PDF` returning no rows for a vault
    full of PDFs reads as "none catalogued" rather than "wrong case".
    """
    lowered = extension.lower()
    return lowered if lowered.startswith(".") else f".{lowered}"


def evaluate(value: object, metadata_filter: MetadataFilter) -> bool:
    """True when `value` satisfies the filter. A missing value never matches."""
    if value is None:
        return False
    op = metadata_filter.op
    if op == "in":
        return any(_equal(value, candidate) for candidate in metadata_filter.values)
    if op == "between":
        low, high = metadata_filter.values
        return _compare(value, low) >= 0 and _compare(value, high) <= 0
    if op == "eq":
        return _equal(value, metadata_filter.values[0])
    ordering = _compare(value, metadata_filter.values[0])
    return {
        "gt": ordering > 0,
        "gte": ordering >= 0,
        "lt": ordering < 0,
        "lte": ordering <= 0,
    }[op]


def _equal(value: object, target: str | float) -> bool:
    return _compare(value, target) == 0


def _compare(value: object, target: str | float) -> int:
    """-1 / 0 / 1, comparing numerically when both sides are numbers."""
    left, right = _coerce(value, target)
    if left == right:
        return 0
    return -1 if left < right else 1


def _coerce(value: object, target: str | float) -> tuple[float, float] | tuple[str, str]:
    left_number = _as_number(value)
    right_number = _as_number(target)
    if left_number is not None and right_number is not None:
        return left_number, right_number
    return str(value), str(target)


def _as_number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None
