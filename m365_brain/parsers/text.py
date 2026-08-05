"""Text primitives shared by the parsers and the index.

Three pure functions with no config surface: a slug, a file digest, and a text
digest. They are here rather than inline because the sync loop, the permalink
fallback and the vector chunker each need one of them, and three copies of a
`sha256` call is how two of them silently drift apart.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from pathlib import Path

_NON_SLUG_RE = re.compile(r"[^\w\s\-]")
_SEPARATOR_RE = re.compile(r"[\s_]+")


def slugify(text: str) -> str:
    """Fold to ASCII, drop punctuation, collapse whitespace and underscores to hyphens.

    Characters with no ASCII decomposition are dropped rather than transliterated,
    so a title in a non-Latin script slugifies to the empty string. The caller
    decides what to do about that -- silently inventing a slug would produce a
    permalink nobody can trace back to a file.
    """
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = _NON_SLUG_RE.sub("", text).strip().lower()
    return _SEPARATOR_RE.sub("-", text)


def file_checksum(path: Path) -> str:
    """SHA-256 of a file's bytes -- the incremental sync's change signal."""
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


def content_hash(text: str) -> str:
    """SHA-256 of a string -- the vector chunker's change signal."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
