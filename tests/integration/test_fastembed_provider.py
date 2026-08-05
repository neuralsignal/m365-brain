"""The real embedding model, behind the `integration` marker.

`pixi run test` filters this out, so the default suite never downloads a few
hundred megabytes. It exists because exactly one claim cannot be checked
offline: that the configured model's vector width is the width the vector store
was created at. Everything else about the adapter is unit-tested with a stub.

    pixi run pytest tests/integration/test_fastembed_provider.py -m integration
"""

from __future__ import annotations

import pytest

from m365_brain.config.index import IndexConfig
from m365_brain.index.vector import create_embedding_provider

pytestmark = pytest.mark.integration

MODEL = "BAAI/bge-small-en-v1.5"
DIMENSIONS = 384


@pytest.fixture()
def provider(index_payload):
    index_payload["vector"]["provider"] = "fastembed"
    index_payload["vector"]["model"] = MODEL
    index_payload["vector"]["dimensions"] = DIMENSIONS
    return create_embedding_provider(IndexConfig.model_validate(index_payload))


def test_the_model_produces_the_configured_width(provider):
    """The one claim no stub can make: the real model agrees with config."""
    assert len(provider.embed_query("a query")) == DIMENSIONS


def test_documents_embed_at_the_configured_width(provider):
    embeddings = provider.embed_documents(["first document", "second document"])
    assert [len(embedding) for embedding in embeddings] == [DIMENSIONS, DIMENSIONS]


def test_embedding_is_deterministic(provider):
    assert provider.embed_query("stable text") == provider.embed_query("stable text")


def test_similar_text_is_nearer_than_unrelated_text(provider):
    """The property the fake explicitly cannot have, asserted where it is real."""
    anchor = provider.embed_query("a cat sat on the mat")
    near, far = provider.embed_documents(["a kitten sat on a rug", "quarterly depreciation schedule"])
    assert _distance(anchor, near) < _distance(anchor, far)


def _distance(left, right) -> float:
    return sum((a - b) ** 2 for a, b in zip(left, right, strict=True)) ** 0.5
