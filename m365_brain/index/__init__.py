"""The knowledge index: entities, edges, text search, and the file catalog.

Layer 3, and deliberately blind to Microsoft 365. Nothing under `index/` imports
`m365/`, `extractors/`, or `frontmatter/` -- `scripts/check_structure.py` fails
the build if it does. That single edge is what lets this package index an
ordinary folder of markdown that nobody synced from anywhere.
"""

from __future__ import annotations

from m365_brain.index.backends import IndexBackend, MetadataFilter, TextQuery, create_index_backend
from m365_brain.index.sync import sync_index

__all__ = ["IndexBackend", "MetadataFilter", "TextQuery", "create_index_backend", "sync_index"]
