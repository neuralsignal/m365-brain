"""Chunk, embed, write, prune -- six phases with the I/O deliberately clustered.

Phases 3 and 4 touch neither the store nor the index: the whole corpus is
chunked and hashed in memory, then embedded in one call. That is what keeps the
store's write lock free during the slow part, and it is why an incremental run
over an unchanged corpus does zero embedding work rather than a little.

The prune is last and unconditional, including on an incremental run. A
document that shrank leaves chunk rows numbered past its new end, and those
rows keep answering queries with text the file no longer contains.
"""

from __future__ import annotations

import time

import structlog

from m365_brain.config.index import VectorConfig
from m365_brain.index.backends.base import IndexBackend
from m365_brain.index.vector.base import EmbeddingProvider, VectorStore
from m365_brain.index.vector.chunking import chunk_key_for, split_into_chunks
from m365_brain.model import Chunk, VectorSyncStats
from m365_brain.parsers.text import content_hash

log = structlog.get_logger()


def sync_vectors(
    config: VectorConfig,
    backend: IndexBackend,
    provider: EmbeddingProvider,
    store: VectorStore,
    full_rebuild: bool,
) -> VectorSyncStats:
    """Bring the vector store in line with the text index."""
    started = time.monotonic()

    store.initialize(config.dimensions)
    if full_rebuild:
        store.clear()

    texts = dict(backend.iter_indexed_text())
    stored_hashes = {} if full_rebuild else store.chunk_hashes()

    chunks: list[Chunk] = []
    expected_counts: dict[int, int] = {}
    for entity_id, text in texts.items():
        entity_chunks = split_into_chunks(text, config.chunk_size, config.chunk_overlap)
        expected_counts[entity_id] = len(entity_chunks)
        known = stored_hashes.get(entity_id, {})
        for index, chunk_text in enumerate(entity_chunks):
            key = chunk_key_for(index)
            digest = content_hash(chunk_text)
            if known.get(key) == digest:
                continue
            chunks.append(Chunk(entity_id=entity_id, chunk_key=key, text=chunk_text, content_hash=digest))

    embeddings = provider.embed_documents([chunk.text for chunk in chunks]) if chunks else []
    if len(embeddings) != len(chunks):
        raise ValueError(
            f"embedding provider returned {len(embeddings)} vectors for {len(chunks)} chunks; "
            f"the pairing in write_chunks is positional and cannot recover from this"
        )
    store.write_chunks(chunks, embeddings)

    pruned = store.prune(set(texts), expected_counts)

    stats = VectorSyncStats(
        entities=len(texts),
        chunks_embedded=len(chunks),
        chunks_written=len(chunks),
        pruned=pruned,
        elapsed_seconds=round(time.monotonic() - started, 3),
    )
    log.info(
        "index.vector.sync_complete",
        entities=stats.entities,
        chunks_embedded=stats.chunks_embedded,
        stale_pruned=pruned.stale,
        tail_pruned=pruned.tail,
        orphan_embeddings_pruned=pruned.orphan_embeddings,
        elapsed_seconds=stats.elapsed_seconds,
    )
    return stats
