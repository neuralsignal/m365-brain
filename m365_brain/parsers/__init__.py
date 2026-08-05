"""Markdown parsing: text in, `model` dataclasses out.

Layer 1. These modules read `config` and write `model`, and nothing else --
no filesystem walking beyond the one file handed to `parse_markdown_file`, no
database, and no knowledge of where the corpus came from. That is what lets the
same parsers index a folder somebody made by hand.
"""

from __future__ import annotations

from m365_brain.parsers.document import parse_markdown_file
from m365_brain.parsers.frontmatter import extract_tags, parse_frontmatter
from m365_brain.parsers.observations import parse_observations
from m365_brain.parsers.relations import parse_relations
from m365_brain.parsers.text import content_hash, file_checksum, slugify

__all__ = [
    "content_hash",
    "extract_tags",
    "file_checksum",
    "parse_frontmatter",
    "parse_markdown_file",
    "parse_observations",
    "parse_relations",
    "slugify",
]
