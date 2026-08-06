"""`SqliteIndexBackend` -- the connection contract, plus dispatch.

The connection policy is the whole reason this module is separate from the SQL:

* **writers** get the configured journal mode and commit on success, roll back
  on exception, and always close. A connection is opened per operation and the
  write lock is held for one operation only.
* **readers** get `PRAGMA query_only=ON` rather than a `mode=ro` URI. The URI
  form also blocks SQLite's own journal recovery, so a reader arriving after an
  unclean shutdown fails instead of recovering.
* `PRAGMA foreign_keys=ON` on both, because the cascade deletes depend on it and
  SQLite defaults it off.

`initialize()` is cached on the instance, not in a module global. A global
survives across test cases, which is why the previous incarnation of this code
needed tests to reach in and reset it.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path

from m365_brain.config.index import IndexConfig
from m365_brain.index.backends import sqlite_catalog as catalog
from m365_brain.index.backends import sqlite_read as read
from m365_brain.index.backends import sqlite_schema as schema
from m365_brain.index.backends import sqlite_write as write
from m365_brain.index.backends.base import TextQuery
from m365_brain.model import (
    CatalogEntry,
    CatalogQuery,
    Entity,
    EntityRef,
    IndexedFile,
    Observation,
    RelationEdge,
    SearchPage,
)


class SqliteIndexBackend:
    """SQLite + FTS5. One connection per operation, short write transactions."""

    def __init__(self, config: IndexConfig) -> None:
        self._config = config
        self._path = Path(config.sqlite.path)
        self._initialized = False

    @contextmanager
    def connect(self, readonly: bool) -> Iterator[sqlite3.Connection]:
        """A configured connection. Public so tests can assert the pragmas."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(f"file:{self._path}", uri=True)
        conn.row_factory = sqlite3.Row
        if readonly:
            conn.execute("PRAGMA query_only=ON")
        else:
            conn.execute(f"PRAGMA journal_mode={self._config.sqlite.journal_mode}")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(f"PRAGMA busy_timeout={self._config.sqlite.busy_timeout_ms}")
        try:
            yield conn
            if not readonly:
                conn.commit()
        except Exception:
            if not readonly:
                conn.rollback()
            raise
        finally:
            conn.close()

    def initialize(self) -> None:
        if self._initialized:
            return
        with self.connect(readonly=False) as conn:
            schema.initialize(conn)
        self._initialized = True

    def close(self) -> None:
        """Nothing to release: connections do not outlive an operation."""
        return None

    # -- write path -------------------------------------------------------

    def indexed_files(self) -> dict[str, IndexedFile]:
        with self.connect(readonly=True) as conn:
            return write.indexed_files(conn)

    def permalink_owners(self) -> dict[str, str]:
        with self.connect(readonly=True) as conn:
            return write.permalink_owners(conn)

    def upsert_entities(self, entities: Sequence[Entity]) -> None:
        with self.connect(readonly=False) as conn:
            write.upsert_entities(conn, entities)

    def delete_entities(self, entity_keys: Sequence[str]) -> int:
        with self.connect(readonly=False) as conn:
            return write.delete_entities(conn, entity_keys)

    def resolve_relations(self) -> int:
        with self.connect(readonly=False) as conn:
            return write.resolve_relations(conn)

    def rebuild_text_index(self) -> None:
        with self.connect(readonly=False) as conn:
            write.rebuild_text_index(conn)

    # -- read path --------------------------------------------------------

    def find_entity(self, identifier: str, by_permalink: bool) -> EntityRef | None:
        with self.connect(readonly=True) as conn:
            return read.find_entity(conn, identifier, by_permalink)

    def get_observations(self, entity_id: int) -> list[Observation]:
        with self.connect(readonly=True) as conn:
            return read.get_observations(conn, entity_id)

    def outgoing_relations(self, entity_ids: Sequence[int]) -> list[RelationEdge]:
        with self.connect(readonly=True) as conn:
            return read.outgoing_relations(conn, entity_ids)

    def incoming_relations(self, entity_ids: Sequence[int]) -> list[RelationEdge]:
        with self.connect(readonly=True) as conn:
            return read.incoming_relations(conn, entity_ids)

    def text_search(self, query: TextQuery) -> SearchPage:
        with self.connect(readonly=True) as conn:
            return read.text_search(conn, query, self._config.search)

    def recent_entities(self, updated_since: str, entity_type: str | None, limit: int) -> list[EntityRef]:
        with self.connect(readonly=True) as conn:
            return read.recent_entities(conn, updated_since, entity_type, limit)

    def count_recent_entities(self, updated_since: str, entity_type: str | None) -> int:
        with self.connect(readonly=True) as conn:
            return read.count_recent_entities(conn, updated_since, entity_type)

    def hydrate(self, entity_ids: Sequence[int]) -> dict[int, EntityRef]:
        with self.connect(readonly=True) as conn:
            return read.hydrate(conn, entity_ids)

    def iter_indexed_text(self) -> Iterator[tuple[int, str]]:
        with self.connect(readonly=True) as conn:
            return read.iter_indexed_text(conn)

    # -- file catalog -----------------------------------------------------

    def upsert_catalog_entry(self, entry: CatalogEntry) -> int:
        with self.connect(readonly=False) as conn:
            return catalog.upsert_catalog_entry(conn, entry)

    def search_catalog(self, query: CatalogQuery) -> list[CatalogEntry]:
        with self.connect(readonly=True) as conn:
            return catalog.search_catalog(conn, query)

    def count_catalog(self, query: CatalogQuery) -> int:
        with self.connect(readonly=True) as conn:
            return catalog.count_catalog(conn, query)

    def get_catalog_entry(self, original_path: str) -> CatalogEntry | None:
        with self.connect(readonly=True) as conn:
            return catalog.get_catalog_entry(conn, original_path)

    def get_catalog_entry_by_id(self, entry_id: int) -> CatalogEntry | None:
        with self.connect(readonly=True) as conn:
            return catalog.get_catalog_entry_by_id(conn, entry_id)

    def set_catalog_status(self, original_path: str, state: str, output_path: str | None, error: str | None) -> None:
        with self.connect(readonly=False) as conn:
            catalog.set_catalog_status(conn, original_path, state, output_path, error)

    def remove_catalog_entry(self, original_path: str) -> bool:
        with self.connect(readonly=False) as conn:
            return catalog.remove_catalog_entry(conn, original_path)

    def catalog_stats(self) -> dict[str, int]:
        with self.connect(readonly=True) as conn:
            return catalog.catalog_stats(conn, self._config.catalog.conversion_states)
