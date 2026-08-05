"""Index backends and their factory.

`index.backend` is a config value, not a code path. Adding a store means adding
an adapter and one `Literal` member -- never a branch inside a caller.
"""

from __future__ import annotations

from m365_brain.config.errors import ConfigError
from m365_brain.config.index import IndexConfig
from m365_brain.index.backends.base import IndexBackend, MetadataFilter, TextQuery


def create_index_backend(config: IndexConfig) -> IndexBackend:
    """Build the configured backend. Crashes on an unknown name."""
    if config.backend == "sqlite":
        from m365_brain.index.backends.sqlite import SqliteIndexBackend

        return SqliteIndexBackend(config)

    if config.backend == "memory":
        from m365_brain.index.backends.memory import InMemoryIndexBackend

        return InMemoryIndexBackend(config)

    raise ConfigError(f"unknown index backend {config.backend!r}")


__all__ = ["IndexBackend", "MetadataFilter", "TextQuery", "create_index_backend"]
