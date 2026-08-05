"""The offline fakes: a deterministic embedder and a dictionary vector store.

`HashEmbeddingProvider` seeds a PRNG from the text's digest, so the same string
always yields the same unit vector and two different strings almost never do.
That is enough for every property the sync and the search care about -- identity,
determinism, dimension -- and it downloads nothing, which is why the whole test
suite can exercise the vector path.

What it is not is *semantic*: `"cat"` and `"kitten"` are as far apart as `"cat"`
and `"cathedral"`. No test may assert that a near-synonym ranks highly; that is
a claim about a real model.
"""

from __future__ import annotations

import math
import random
from collections.abc import Mapping, Sequence, Set

from m365_brain.index.vector.chunking import chunk_index
from m365_brain.model import Chunk, PruneStats, VectorHit
from m365_brain.parsers.text import content_hash


class HashEmbeddingProvider:
    """`sha256(text)` seeds a PRNG; the draw is L2-normalized."""

    def __init__(self, dimensions: int) -> None:
        self._dimensions = dimensions

    @property
    def dimensions(self) -> int:
        return self._dimensions

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    def _embed(self, text: str) -> list[float]:
        rng = random.Random(content_hash(text))
        raw = [rng.gauss(0.0, 1.0) for _ in range(self._dimensions)]
        norm = math.sqrt(sum(value * value for value in raw))
        if norm == 0.0:
            # Vanishingly unlikely, and a zero vector has no direction: every
            # distance to it is the same, which would look like a ranking bug.
            return [1.0] + [0.0] * (self._dimensions - 1)
        return [value / norm for value in raw]


class InMemoryVectorStore:
    """`{entity id: {chunk key: (hash, vector)}}`, with brute-force search."""

    def __init__(self) -> None:
        self._chunks: dict[int, dict[str, tuple[str, list[float]]]] = {}
        self._dimensions: int | None = None

    def initialize(self, dimensions: int) -> None:
        self._dimensions = dimensions

    def close(self) -> None:
        return None

    def clear(self) -> None:
        self._chunks = {}

    def chunk_hashes(self) -> dict[int, dict[str, str]]:
        return {
            entity_id: {chunk_key: stored_hash for chunk_key, (stored_hash, _vector) in keys.items()}
            for entity_id, keys in self._chunks.items()
        }

    def write_chunks(self, chunks: Sequence[Chunk], embeddings: Sequence[Sequence[float]]) -> None:
        if len(chunks) != len(embeddings):
            raise ValueError(f"write_chunks got {len(chunks)} chunks and {len(embeddings)} embeddings")
        for chunk, embedding in zip(chunks, embeddings, strict=True):
            if self._dimensions is not None and len(embedding) != self._dimensions:
                raise ValueError(
                    f"embedding for {chunk.entity_id}/{chunk.chunk_key} has {len(embedding)} dimensions, "
                    f"store was initialized at {self._dimensions}"
                )
            self._chunks.setdefault(chunk.entity_id, {})[chunk.chunk_key] = (chunk.content_hash, list(embedding))

    def prune(self, live_entity_ids: Set[int], expected_chunk_counts: Mapping[int, int]) -> PruneStats:
        stale = 0
        tail = 0
        for entity_id in list(self._chunks):
            if entity_id not in live_entity_ids:
                stale += len(self._chunks.pop(entity_id))
                continue
            expected = expected_chunk_counts.get(entity_id, 0)
            for chunk_key in list(self._chunks[entity_id]):
                if chunk_index(chunk_key) >= expected:
                    del self._chunks[entity_id][chunk_key]
                    tail += 1
        # Chunks and vectors are the same dict entry here, so an embedding can
        # never outlive its chunk. The counter stays in the result because the
        # store that keeps them in two tables can, and both report one shape.
        return PruneStats(stale=stale, tail=tail, orphan_embeddings=0)

    def query(self, embedding: Sequence[float], k: int) -> list[VectorHit]:
        hits = [
            VectorHit(entity_id=entity_id, chunk_key=chunk_key, distance=_distance(embedding, vector))
            for entity_id, keys in self._chunks.items()
            for chunk_key, (_stored_hash, vector) in keys.items()
        ]
        hits.sort(key=lambda hit: (hit.distance, hit.entity_id, hit.chunk_key))
        return hits[:k]


def _distance(left: Sequence[float], right: Sequence[float]) -> float:
    """Euclidean distance -- what the real store's `vec0` tables report."""
    return math.sqrt(sum((a - b) ** 2 for a, b in zip(left, right, strict=True)))
