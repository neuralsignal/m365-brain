"""The two vector protocols: who turns text into numbers, and who stores them.

They are separate because they fail differently. An embedding provider is a
model download, a thread cap and a dimension count; a vector store is a file, a
schema and a nearest-neighbour query. Pairing them in one interface would mean
an offline fake had to fake both halves at once, and the interesting bugs --
churn in the prune, a dimension mismatch -- each live in exactly one of them.

`prune` takes `expected_chunk_counts` as a mapping of integers rather than
letting the store work the counts out itself. The store that did work them out
compared `chunk_key` as text, so `'chunk_9' > 'chunk_10'` deleted valid chunks
from every entity with ten or more of them; they re-embedded on the next run,
and the loop was permanent. Handing the numbers in makes the comparison
numeric by construction in any implementation.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence, Set
from typing import Protocol, runtime_checkable

from m365_brain.model import Chunk, PruneStats, VectorHit


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Text in, vectors out. Nothing about storage."""

    @property
    def dimensions(self) -> int:
        """Vector width. Checked against the configured width at construction.

        A model whose width disagrees with the store's otherwise surfaces as an
        insert error thousands of chunks into a rebuild.
        """
        ...

    def embed_query(self, text: str) -> list[float]:
        """One search query. Some models embed queries and documents differently."""
        ...

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """A batch of documents, in the order given."""
        ...


@runtime_checkable
class VectorStore(Protocol):
    """Chunks and their embeddings, plus nearest-neighbour lookup."""

    def initialize(self, dimensions: int) -> None:
        """Create whatever the store needs, at this vector width. Idempotent."""
        ...

    def close(self) -> None:
        """Release resources. Safe to call twice."""
        ...

    def clear(self) -> None:
        """Drop every chunk and embedding -- what `full_rebuild` means here."""
        ...

    def chunk_hashes(self) -> dict[int, dict[str, str]]:
        """`{entity id: {chunk key: content hash}}` for everything stored.

        The sync compares these to freshly computed hashes; anything equal is
        not re-embedded.
        """
        ...

    def write_chunks(self, chunks: Sequence[Chunk], embeddings: Sequence[Sequence[float]]) -> None:
        """Insert or replace chunks positionally paired with their embeddings."""
        ...

    def prune(self, live_entity_ids: Set[int], expected_chunk_counts: Mapping[int, int]) -> PruneStats:
        """Remove what no longer belongs, in three separate senses.

        * **stale** -- every chunk of an entity that is no longer indexed
        * **tail** -- chunks numbered at or past an entity's current chunk count,
          left behind when a document shrank
        * **orphan embeddings** -- embedding rows whose chunk row is gone. They
          are invisible to every query but scanned by all of them.

        The three are counted separately because a wrong number in each is a
        different bug.
        """
        ...

    def query(self, embedding: Sequence[float], k: int) -> list[VectorHit]:
        """The `k` nearest chunks, closest first."""
        ...
