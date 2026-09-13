"""The fastembed adapter, without downloading a model.

Everything asserted here is config wiring and the width guard. Whether the real
model works is `tests/integration/test_fastembed_provider.py`'s question, and it
is marked `integration` so the default suite never pulls a few hundred megabytes.

The laziness being pinned is deliberate: constructing `TextEmbedding` downloads,
so a workspace that is opened and never searched must not construct one.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from m365_brain.index.vector import create_embedding_provider
from m365_brain.index.vector.base import EmbeddingProvider
from m365_brain.index.vector.fastembed_provider import FastembedProvider


class FakeModel:
    """Stands in for `TextEmbedding`, returning vectors of a chosen width."""

    def __init__(self, width: int) -> None:
        self.width = width
        self.batch_sizes: list[int] = []

    def query_embed(self, text: str):
        return iter([_Array([0.0] * self.width)])

    def passage_embed(self, texts, batch_size: int):
        self.batch_sizes.append(batch_size)
        return [_Array([0.0] * self.width) for _ in texts]


class _Array:
    def __init__(self, values: list[float]) -> None:
        self._values = values

    def tolist(self) -> list[float]:
        return self._values


@pytest.fixture()
def provider(index_config) -> FastembedProvider:
    return FastembedProvider(index_config.vector)


def test_it_satisfies_the_protocol(provider):
    assert isinstance(provider, EmbeddingProvider)


def test_the_model_is_not_loaded_at_construction(provider):
    """Constructing `TextEmbedding` downloads; opening a workspace must not."""
    assert provider._model is None


def test_width_comes_from_config(provider, index_config):
    assert provider.dimensions == index_config.vector.dimensions


def test_the_factory_builds_it_when_configured(index_payload):
    index_payload["vector"]["provider"] = "fastembed"
    from m365_brain.config.index import IndexConfig

    assert isinstance(create_embedding_provider(IndexConfig.model_validate(index_payload)), FastembedProvider)


def test_documents_embed_at_the_configured_batch_size(provider, index_config):
    provider._model = FakeModel(index_config.vector.dimensions)
    provider.embed_documents(["a", "b"])
    assert provider._model.batch_sizes == [index_config.vector.embed_batch_size]


def test_a_model_wider_than_config_is_rejected_on_the_first_batch(provider, index_config):
    """Not thousands of chunks later, inside an opaque insert error."""
    provider._model = FakeModel(index_config.vector.dimensions + 1)
    with pytest.raises(ValueError, match="index.vector.dimensions"):
        provider.embed_documents(["a"])


def test_a_wrong_width_query_is_rejected_too(provider, index_config):
    provider._model = FakeModel(index_config.vector.dimensions - 1)
    with pytest.raises(ValueError, match="index.vector.dimensions"):
        provider.embed_query("a")


def test_load_constructs_text_embedding_with_config(provider, index_config):
    """First call to _load() imports and constructs TextEmbedding with config values."""
    sentinel = FakeModel(index_config.vector.dimensions)

    with patch("fastembed.TextEmbedding", return_value=sentinel) as mock_cls:
        provider.embed_query("hello")

    mock_cls.assert_called_once_with(
        model_name=index_config.vector.model,
        threads=index_config.vector.threads,
    )


def test_load_memoises_the_model(provider, index_config):
    """Second call must reuse the model, not construct another."""
    sentinel = FakeModel(index_config.vector.dimensions)

    with patch("fastembed.TextEmbedding", return_value=sentinel) as mock_cls:
        provider.embed_query("first")
        provider.embed_query("second")

    mock_cls.assert_called_once()
