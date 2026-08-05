"""Embedding providers and vector stores, plus their factories.

Both factories are total: they build what `index.vector.provider` and
`index.vector.store` name, or they crash. Whether vectors are wanted at all is
`index.vector.enabled`, and that question is answered once, by the caller that
would own the objects -- a factory returning `None` puts the same check in
every call site instead.

Neither factory checks the provider's vector width against
`index.vector.dimensions`, because at construction neither provider knows it:
the local model is loaded lazily, so asking would download it. The check lives
in `FastembedProvider`, on the first batch it embeds -- early enough to precede
any write, and the first moment the real number exists.
"""

from __future__ import annotations

from m365_brain.config.errors import ConfigError
from m365_brain.config.index import IndexConfig
from m365_brain.index.vector.base import EmbeddingProvider, VectorStore
from m365_brain.index.vector.chunking import chunk_index, chunk_key_for, split_into_chunks
from m365_brain.index.vector.memory import HashEmbeddingProvider, InMemoryVectorStore
from m365_brain.index.vector.sync import sync_vectors


def create_embedding_provider(config: IndexConfig) -> EmbeddingProvider:
    """Build the configured provider. Crashes on an unknown name."""
    if config.vector.provider == "fastembed":
        from m365_brain.index.vector.fastembed_provider import FastembedProvider

        return FastembedProvider(config.vector)

    if config.vector.provider == "hash":
        return HashEmbeddingProvider(config.vector.dimensions)

    raise ConfigError(f"unknown index.vector.provider {config.vector.provider!r}")


def create_vector_store(config: IndexConfig) -> VectorStore:
    """Build the configured store. Crashes on an unknown name.

    Takes the whole `index:` section because the SQL store lives in the same
    database file as the entities it points at -- vectors in a second file
    could not be pruned against a live entity set without opening both.
    """
    if config.vector.store == "sqlite_vec":
        from m365_brain.index.vector.sqlite_vec_store import SqliteVecStore

        return SqliteVecStore(config.sqlite, config.vector)

    if config.vector.store == "memory":
        return InMemoryVectorStore()

    raise ConfigError(f"unknown index.vector.store {config.vector.store!r}")


__all__ = [
    "EmbeddingProvider",
    "HashEmbeddingProvider",
    "InMemoryVectorStore",
    "VectorStore",
    "chunk_index",
    "chunk_key_for",
    "create_embedding_provider",
    "create_vector_store",
    "split_into_chunks",
    "sync_vectors",
]
