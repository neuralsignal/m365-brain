"""Markdown file writer with YAML frontmatter. Handles slugification and deduplication hashing.

Frontmatter builder functions live in ``m365_extract.frontmatter``.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import UTC, datetime

import frontmatter


def slugify(text: str, max_length: int) -> str:
    """Convert text to a filesystem-safe slug.

    Lowercases, strips accents, replaces non-alphanumeric with hyphens,
    collapses runs of hyphens, and trims to max_length.
    """
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = text.strip("-")
    if len(text) > max_length:
        text = text[:max_length].rstrip("-")
    return text or "untitled"


def short_hash(text: str, length: int) -> str:
    """Return a deterministic short hex hash of the input text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:length]


def now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def dumps_markdown(metadata: dict, body: str) -> str:
    """Serialize metadata + body to a frontmatter markdown string."""
    post = frontmatter.Post(body, **metadata)
    return frontmatter.dumps(post)


def loads_markdown(content: str) -> tuple[dict, str]:
    """Parse a frontmatter markdown string. Returns (metadata_dict, body_string)."""
    post = frontmatter.loads(content)
    return dict(post.metadata), post.content
