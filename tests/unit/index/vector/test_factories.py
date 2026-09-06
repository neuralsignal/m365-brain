"""Tests for the create_embedding_provider and create_vector_store factories."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from m365_brain.config.errors import ConfigError
from m365_brain.config.index import IndexConfig
from m365_brain.index.vector import create_embedding_provider, create_vector_store

KNOWN_PROVIDERS = {"fastembed", "hash"}
KNOWN_STORES = {"sqlite_vec", "memory"}


def _with_provider(index_payload: dict, name: str) -> IndexConfig:
    """Build an IndexConfig, then force an invalid provider past Pydantic."""
    config = IndexConfig.model_validate(index_payload)
    object.__setattr__(config.vector, "provider", name)
    return config


def _with_store(index_payload: dict, name: str) -> IndexConfig:
    """Build an IndexConfig, then force an invalid store past Pydantic."""
    config = IndexConfig.model_validate(index_payload)
    object.__setattr__(config.vector, "store", name)
    return config


def test_unknown_provider_raises_config_error(index_payload: dict) -> None:
    config = _with_provider(index_payload, "nonexistent")
    with pytest.raises(ConfigError, match="unknown index.vector.provider"):
        create_embedding_provider(config)


def test_unknown_store_raises_config_error(index_payload: dict) -> None:
    config = _with_store(index_payload, "nonexistent")
    with pytest.raises(ConfigError, match="unknown index.vector.store"):
        create_vector_store(config)


@given(name=st.text(min_size=1).filter(lambda s: s not in KNOWN_PROVIDERS))
def test_arbitrary_provider_raises_config_error(name: str) -> None:
    import tempfile
    from pathlib import Path

    from tests.unit.conftest import index_payload_for

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        corpus = tmp_path / "corpus"
        corpus.mkdir()
        payload = index_payload_for(
            tmp_path / "index.db",
            [{"name": "corpus", "path": str(corpus), "recursive": True}],
        )
        config = IndexConfig.model_validate(payload)
        object.__setattr__(config.vector, "provider", name)
        with pytest.raises(ConfigError, match="unknown index.vector.provider"):
            create_embedding_provider(config)


@given(name=st.text(min_size=1).filter(lambda s: s not in KNOWN_STORES))
def test_arbitrary_store_raises_config_error(name: str) -> None:
    import tempfile
    from pathlib import Path

    from tests.unit.conftest import index_payload_for

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        corpus = tmp_path / "corpus"
        corpus.mkdir()
        payload = index_payload_for(
            tmp_path / "index.db",
            [{"name": "corpus", "path": str(corpus), "recursive": True}],
        )
        config = IndexConfig.model_validate(payload)
        object.__setattr__(config.vector, "store", name)
        with pytest.raises(ConfigError, match="unknown index.vector.store"):
            create_vector_store(config)
