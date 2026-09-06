"""Factory guard: unknown backend names raise ConfigError."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from m365_brain.config.errors import ConfigError
from m365_brain.config.index import IndexConfig
from m365_brain.index.backends import create_index_backend

KNOWN_BACKENDS = {"sqlite", "memory"}


def _config_with_backend(name: str, index_payload: dict) -> IndexConfig:
    """Build an IndexConfig with an arbitrary backend name, bypassing Literal validation."""
    index_payload["backend"] = "memory"
    config = IndexConfig.model_validate(index_payload)
    object.__setattr__(config, "backend", name)
    return config


def test_unknown_backend_raises_config_error(index_payload):
    config = _config_with_backend("nonexistent", index_payload)
    with pytest.raises(ConfigError, match="unknown index backend 'nonexistent'"):
        create_index_backend(config)


@given(name=st.text(min_size=1).filter(lambda s: s not in KNOWN_BACKENDS))
def test_any_non_known_backend_raises_config_error(name):
    config = IndexConfig.model_construct(backend=name)
    with pytest.raises(ConfigError, match="unknown index backend"):
        create_index_backend(config)
