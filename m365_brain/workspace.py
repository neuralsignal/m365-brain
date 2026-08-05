"""`Workspace` -- config in, a working handle out.

Composition and nothing else. Every method here is one call to the module that
owns the behaviour, and the reason to keep it that thin is that it is the
surface consumers migrate onto: any logic that lives here is logic they cannot
reach any other way, and any behaviour it invents is behaviour the underlying
modules cannot be tested for.

It builds three things from config and holds them: an index backend, and -- when
`index.vector.enabled` is true -- an embedding provider and a vector store. When
vectors are off both are `None`, and the operations that need them raise naming
the config key rather than degrading into a text search.

No method takes a default argument. `full_rebuild`, `mode`, `page` and
`max_depth` all change what the call does, and a call site that does not say
which it wants has not decided.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import TracebackType

from m365_brain.config import Config, load_config, require_section
from m365_brain.config.index import IndexConfig
from m365_brain.index.backends import IndexBackend, create_index_backend
from m365_brain.index.catalog import FileCatalog
from m365_brain.index.graph import find, observations, traverse
from m365_brain.index.query import updated_since
from m365_brain.index.search import SearchFilters, SearchMode, search
from m365_brain.index.sync import sync_index
from m365_brain.index.vector import create_embedding_provider, create_vector_store, sync_vectors
from m365_brain.index.vector.base import EmbeddingProvider, VectorStore
from m365_brain.model import EntityRef, GraphEdge, Observation, SearchPage, SyncStats, VectorSyncStats


class Workspace:
    """A configured index, ready to sync, search and traverse."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._index: IndexConfig = require_section(config.index, "index")
        self._backend: IndexBackend = create_index_backend(self._index)
        self._provider: EmbeddingProvider | None = None
        self._store: VectorStore | None = None
        if self._index.vector.enabled:
            self._provider = create_embedding_provider(self._index)
            self._store = create_vector_store(self._index)

    @classmethod
    def open(cls, config_path: str) -> Workspace:
        """Load a config file, build the workspace, and initialize the store."""
        workspace = cls(load_config(config_path))
        workspace._backend.initialize()
        return workspace

    def close(self) -> None:
        """Release the backend and the vector store. Safe to call twice."""
        self._backend.close()
        if self._store is not None:
            self._store.close()

    def __enter__(self) -> Workspace:
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        self.close()

    @property
    def config(self) -> Config:
        return self._config

    @property
    def backend(self) -> IndexBackend:
        """The index backend, for operations the facade does not cover yet.

        A missing method here is a facade gap to fill, not a licence to write
        store-specific queries: the protocol has none to offer.
        """
        return self._backend

    # -- indexing ---------------------------------------------------------

    def sync(self, full_rebuild: bool) -> SyncStats:
        """Bring the index in line with the files under `index.roots`."""
        return sync_index(self._index, self._backend, full_rebuild)

    def sync_vectors(self, full_rebuild: bool) -> VectorSyncStats:
        """Chunk, embed and store every indexed entity's text."""
        provider, store = self._require_vectors("sync_vectors")
        return sync_vectors(self._index.vector, self._backend, provider, store, full_rebuild)

    # -- reading ----------------------------------------------------------

    def search(self, text: str | None, mode: SearchMode, filters: SearchFilters, page: int) -> SearchPage:
        """One page of results at `index.search.page_size`."""
        return search(
            self._index,
            self._backend,
            self._provider,
            self._store,
            text,
            mode,
            filters,
            page,
            self._index.search.page_size,
        )

    def find(self, identifier: str, by_permalink: bool) -> EntityRef | None:
        """One entity by permalink, or by title with alias and partial fallbacks."""
        return find(self._backend, identifier, by_permalink)

    def observations(self, entity_id: int) -> list[Observation]:
        """Every observation recorded for an entity."""
        return observations(self._backend, entity_id)

    def context(self, entity_id: int, max_depth: int) -> list[GraphEdge]:
        """Every edge within `max_depth` hops of an entity, in discovery order."""
        return traverse(self._backend, entity_id, max_depth)

    def recent(self, timeframe: str, limit: int) -> list[EntityRef]:
        """Entities updated within a timeframe such as `7d` or `last week`, newest first."""
        return self._backend.recent_entities(updated_since(timeframe, datetime.now(UTC)), limit)

    def catalog(self) -> FileCatalog:
        """The file catalog, bound to the configured conversion vocabulary."""
        return FileCatalog(self._backend, self._index.catalog)

    # -- internals --------------------------------------------------------

    def _require_vectors(self, operation: str) -> tuple[EmbeddingProvider, VectorStore]:
        if self._provider is None or self._store is None:
            raise ValueError(f"{operation} needs index.vector.enabled: true, and it is false")
        return self._provider, self._store
