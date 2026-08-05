"""Splitting a document into embeddable pieces. Pure, no config object.

The two sizes arrive as arguments rather than as a `VectorConfig`, so the
function is directly property-testable and the caller stays the only place that
reads config.

The strategy is deliberately dumb: cut on markdown headers, split any section
that is still too long line by line with a character overlap, then merge
neighbouring pieces back up towards the size limit. It is the shape that has
been indexing real notes; a smarter splitter would change every stored chunk
hash and re-embed the entire corpus to prove nothing.
"""

from __future__ import annotations

import re

_HEADER_RE = re.compile(r"^#{1,6}\s+", re.MULTILINE)

# A chunk is addressed by its position in the split. The prefix is named here
# because two things invert it -- the fake in Python, the SQL store in a
# `SUBSTR` -- and a literal `7` in one of them is how the numeric comparison
# silently became a text comparison the first time.
CHUNK_KEY_PREFIX = "chunk_"


def chunk_key_for(index: int) -> str:
    """The stable key of the `index`-th chunk of a document."""
    return f"{CHUNK_KEY_PREFIX}{index}"


def chunk_index(chunk_key: str) -> int:
    """The position a chunk key encodes, as a number.

    Comparing keys as text is the bug this exists to prevent: `'chunk_9'` sorts
    after `'chunk_10'`, so a prune bounded by a text comparison deletes live
    chunks from every document long enough to have ten of them.
    """
    if not chunk_key.startswith(CHUNK_KEY_PREFIX):
        raise ValueError(f"not a chunk key: {chunk_key!r}; expected a {CHUNK_KEY_PREFIX!r} prefix")
    return int(chunk_key[len(CHUNK_KEY_PREFIX) :])


def split_into_chunks(text: str, max_chunk_size: int, overlap: int) -> list[str]:
    """Cut `text` into chunks of at most `max_chunk_size` characters.

    Empty or whitespace-only input yields no chunks -- there is nothing to
    embed, and a chunk of spaces is a nearest neighbour to everything.

    A single line longer than `max_chunk_size` is emitted whole: breaking mid-
    token to satisfy a limit costs more meaning than the oversized chunk does.
    """
    if not text.strip():
        return []

    sections = [section.strip() for section in _HEADER_RE.split(text) if section.strip()]

    chunks: list[str] = []
    for section in sections:
        if len(section) <= max_chunk_size:
            chunks.append(section)
        else:
            chunks.extend(_split_long_section(section, max_chunk_size, overlap))

    merged = _merge_short(chunks, max_chunk_size)
    return merged if merged else [text[:max_chunk_size]]


def _split_long_section(section: str, max_chunk_size: int, overlap: int) -> list[str]:
    """Line-by-line accumulation, carrying the last `overlap` characters forward.

    The overlap exists so a sentence straddling a cut is still retrievable from
    the chunk after it.
    """
    chunks: list[str] = []
    current: list[str] = []
    current_length = 0

    for line in section.split("\n"):
        line_length = len(line) + 1  # the newline that rejoining will add back
        if current_length + line_length > max_chunk_size and current:
            chunk_text = "\n".join(current)
            chunks.append(chunk_text)
            carried = chunk_text[-overlap:] if len(chunk_text) > overlap else ""
            current = [carried] if carried else []
            current_length = len(carried)
        current.append(line)
        current_length += line_length

    if current:
        chunks.append("\n".join(current))
    return chunks


def _merge_short(chunks: list[str], max_chunk_size: int) -> list[str]:
    """Glue consecutive chunks together while they still fit.

    Header splitting produces a lot of two-line sections; embedding each one
    separately buries the signal in boilerplate and multiplies the row count.
    """
    merged: list[str] = []
    buffer = ""
    for chunk in chunks:
        if len(buffer) + len(chunk) + 1 <= max_chunk_size:
            buffer = (buffer + "\n" + chunk).strip() if buffer else chunk
        else:
            if buffer:
                merged.append(buffer)
            buffer = chunk
    if buffer:
        merged.append(buffer)
    return merged
