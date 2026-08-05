"""Registration: every binary that reaches storage becomes a catalog row.

**Why a decorator and not a call at each write point.** There are three places
in this package where bytes reach `StorageBackend`, and a fourth would be added
by whoever adds a ninth extractor. Registering at each of them is a rule that
holds until somebody forgets it, and nothing else in the system compares the
files on disk against the rows in the catalog -- which is precisely how the
predecessor ended up with a table nothing ever wrote to.

**Why it is not `RecordingStorage`.** That class decorates the same boundary
for the change manifest, so folding catalog registration into it would be one
mechanism instead of two. It cannot: `manifest` is layer 3 and `index` is layer
5, so `RecordingStorage` may not import `FileCatalog`. What generalises instead
is the *boundary*, not the class -- both are `StorageBackend` decorators, they
stack in either order, and each is composed by a layer that can see both.

**Why an unchanged binary is left alone.** A cycle re-downloading an attachment
it already has would otherwise reset a converted row to `pending` and the next
`extract` would convert it again, forever. Same path and same size means the
same bytes for this purpose, and the row keeps whatever state it reached.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
from pathlib import PurePosixPath

from m365_brain.config.index import IndexConfig
from m365_brain.index.backends import create_index_backend
from m365_brain.index.catalog import FileCatalog
from m365_brain.model import CatalogEntry
from m365_brain.storage.base import StorageBackend


class CatalogingStorage:
    """A `StorageBackend` that catalogs every binary written through it."""

    def __init__(self, inner: StorageBackend, catalog: FileCatalog, source: str, clock: Callable[[], datetime]) -> None:
        self._inner = inner
        self._catalog = catalog
        self._source = source
        self._clock = clock

    def write_bytes(self, path: str, content: bytes) -> None:
        self._inner.write_bytes(path, content)
        existing = self._catalog.get(path)
        if existing is not None and existing.size_bytes == len(content):
            return
        name = PurePosixPath(path)
        # `modified_at` is when the binary landed in the vault, not the mtime
        # upstream reported: the byte stream is all this boundary sees, and
        # inventing a remote timestamp it cannot check would make the column a
        # guess dressed as a fact.
        self._catalog.upsert(
            CatalogEntry(
                entry_id=None,
                original_path=path,
                file_name=name.name,
                extension=name.suffix.lower(),
                source=self._source,
                size_bytes=len(content),
                modified_at=self._clock().isoformat(),
                conversion_status=self._catalog.initial_state,
                output_path=None,
                error=None,
            )
        )

    def delete_file(self, path: str) -> None:
        self._inner.delete_file(path)
        self._catalog.remove(path)

    def write_file(self, path: str, content: str) -> None:
        self._inner.write_file(path, content)

    def read_file(self, path: str) -> str:
        return self._inner.read_file(path)

    def file_exists(self, path: str) -> bool:
        return self._inner.file_exists(path)

    def list_files(self, prefix: str) -> list[str]:
        return self._inner.list_files(prefix)


@contextmanager
def catalog_writes(
    index: IndexConfig | None, inner: StorageBackend, source: str, clock: Callable[[], datetime]
) -> Iterator[StorageBackend]:
    """Wrap `inner` so its binary writes are catalogued for the duration.

    `index is None` yields `inner` untouched: a deployment with no `index:`
    section has no catalog to write to, and that is a configuration, not a
    failure. The backend is opened and closed here because the catalog needs a
    connection for exactly as long as the extractor runs.
    """
    if index is None:
        yield inner
        return
    backend = create_index_backend(index)
    backend.initialize()
    try:
        yield CatalogingStorage(inner, FileCatalog(backend, index.catalog), source, clock)
    finally:
        backend.close()
