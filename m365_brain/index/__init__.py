"""The knowledge index: entities, edges, text search, vectors, and the file catalog.

Layer 3, and deliberately blind to Microsoft 365. Nothing under `index/` imports
`m365/`, `extractors/`, or `frontmatter/` -- `scripts/check_structure.py` fails
the build if it does. That single edge is what lets this package index an
ordinary folder of markdown that nobody synced from anywhere.
"""

from __future__ import annotations

from m365_brain.index.backends import IndexBackend, MetadataFilter, TextQuery, create_index_backend
from m365_brain.index.catalog import FileCatalog
from m365_brain.index.fusion import reciprocal_rank_fusion
from m365_brain.index.graph import find, observations, traverse
from m365_brain.index.query import parse_metadata_filter, parse_timeframe, to_fts_query, updated_since
from m365_brain.index.search import SearchFilters, SearchMode, search
from m365_brain.index.sync import sync_index
from m365_brain.index.vector import (
    EmbeddingProvider,
    VectorStore,
    create_embedding_provider,
    create_vector_store,
    sync_vectors,
)

__all__ = [
    "EmbeddingProvider",
    "FileCatalog",
    "IndexBackend",
    "MetadataFilter",
    "SearchFilters",
    "SearchMode",
    "TextQuery",
    "VectorStore",
    "create_embedding_provider",
    "create_index_backend",
    "create_vector_store",
    "find",
    "observations",
    "parse_metadata_filter",
    "parse_timeframe",
    "reciprocal_rank_fusion",
    "search",
    "sync_index",
    "sync_vectors",
    "to_fts_query",
    "traverse",
    "updated_since",
]
