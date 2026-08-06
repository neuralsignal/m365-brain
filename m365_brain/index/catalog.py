"""The file catalog: what non-markdown files exist and whether they converted.

A thin lifecycle over the backend's catalog methods. Its one piece of judgement
is the conversion vocabulary: `index.catalog.conversion_states` is the whole set
of states a file may be in, and a write naming anything else is rejected here
rather than stored.

That check is the reason this layer exists at all. The version it replaces had
a `mark_converted` and a `mark_conversion_failed` differing only by a literal
string, so the vocabulary was spelled out in the code twice and could not be
extended without a third function.
"""

from __future__ import annotations

from m365_brain.config.index import CatalogConfig
from m365_brain.index.backends.base import IndexBackend
from m365_brain.model import CatalogEntry, CatalogQuery


class FileCatalog:
    """Catalog operations, with every state validated against config."""

    def __init__(self, backend: IndexBackend, config: CatalogConfig) -> None:
        self._backend = backend
        self._config = config

    @property
    def initial_state(self) -> str:
        """The state a newly catalogued file starts in, per config.

        Exposed rather than applied: `CatalogEntry.conversion_status` is a
        required field, so whoever builds the entry reads the state from here
        instead of the catalog quietly overwriting what it was handed.
        """
        return self._config.initial_state

    def upsert(self, entry: CatalogEntry) -> int:
        """Insert or update by `original_path`. Returns the row id."""
        self._require_known_state(entry.conversion_status)
        return self._backend.upsert_catalog_entry(entry)

    def search(self, query: CatalogQuery) -> list[CatalogEntry]:
        """Rows matching every set filter, most recently modified first."""
        if query.status is not None:
            self._require_known_state(query.status)
        return self._backend.search_catalog(query)

    def count(self, query: CatalogQuery) -> int:
        """How many rows match, ignoring `query.limit`.

        What `search` cannot say: a listing that came back holding exactly
        `limit` rows looks identical whether that was all of them or the first
        hundred of nine hundred.
        """
        if query.status is not None:
            self._require_known_state(query.status)
        return self._backend.count_catalog(query)

    def get(self, original_path: str) -> CatalogEntry | None:
        return self._backend.get_catalog_entry(original_path)

    def get_by_id(self, entry_id: int) -> CatalogEntry | None:
        return self._backend.get_catalog_entry_by_id(entry_id)

    def set_status(self, original_path: str, state: str, output_path: str | None, error: str | None) -> None:
        """Move a file to a conversion state, replacing its output path and error.

        Both are replaced, not merged: a file that converts after a failure
        keeps no stale error, and one that fails after converting advertises no
        output that is no longer current.
        """
        self._require_known_state(state)
        self._backend.set_catalog_status(original_path, state, output_path, error)

    def remove(self, original_path: str) -> bool:
        """Delete a row. True when one existed."""
        return self._backend.remove_catalog_entry(original_path)

    def stats(self) -> dict[str, int]:
        """`total` plus one count per configured state, zeros included.

        Every configured state appears whether or not any file is in it: a
        missing key reads as "no such state", which is a different fact from
        "no files there" and sends a caller looking for a bug.
        """
        return self._backend.catalog_stats()

    def _require_known_state(self, state: str) -> None:
        if state not in self._config.conversion_states:
            raise ValueError(
                f"conversion state {state!r} is not in index.catalog.conversion_states {self._config.conversion_states}"
            )
