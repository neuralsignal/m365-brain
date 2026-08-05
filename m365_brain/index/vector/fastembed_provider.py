"""`FastembedProvider` -- a local ONNX embedding model, one per instance.

The model, the thread cap and the batch size are bound at construction from
config. The version this replaces kept all three in module globals guarded by a
"has anything changed?" check, which meant two components wanting different
models silently shared whichever was constructed last.

Loading is deferred to the first embed call because constructing the model
downloads it. A caller that builds a workspace and never searches should not
pull a few hundred megabytes, and the unit tests can assert the config wiring
without network access.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from m365_brain.config.index import VectorConfig


class FastembedProvider:
    """Wraps `fastembed.TextEmbedding`. Queries and documents embed differently."""

    def __init__(self, config: VectorConfig) -> None:
        self._model_name = config.model
        self._threads = config.threads
        self._batch_size = config.embed_batch_size
        self._dimensions = config.dimensions
        self._model: Any = None

    @property
    def dimensions(self) -> int:
        """The width from `index.vector.dimensions`, which the model must agree with."""
        return self._dimensions

    def embed_query(self, text: str) -> list[float]:
        return self._checked([next(iter(self._load().query_embed(text))).tolist()])[0]

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        embeddings = self._load().passage_embed(list(texts), batch_size=self._batch_size)
        return self._checked([embedding.tolist() for embedding in embeddings])

    def _load(self) -> Any:
        if self._model is None:
            from fastembed import TextEmbedding

            self._model = TextEmbedding(model_name=self._model_name, threads=self._threads)
        return self._model

    def _checked(self, embeddings: list[list[float]]) -> list[list[float]]:
        """Reject a model whose width contradicts config, on the first batch.

        The vector store's table is created at the configured width, so a
        mismatch would otherwise surface as an opaque insert error somewhere
        inside a rebuild, naming neither the model nor the config key.
        """
        for embedding in embeddings:
            if len(embedding) != self._dimensions:
                raise ValueError(
                    f"index.vector.model {self._model_name!r} produces {len(embedding)}-dimensional "
                    f"vectors, but index.vector.dimensions is {self._dimensions}"
                )
        return embeddings
