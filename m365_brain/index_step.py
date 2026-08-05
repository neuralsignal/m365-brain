"""The index half of a cycle: open the backend, sync, embed, close, report.

Split out of `cycle.py` for size, and the seam is a real one: this is the only
part of a cycle that touches the index, it owns the backend's whole lifecycle
within one call, and it converts an index run into the `IndexOutcome` the
manifest carries. Nothing here knows that Microsoft 365 exists.

Embedding runs inside this call rather than on a background thread. A cycle
that reports done while a thread is still writing the vector store is a cycle
that lies, and the thread needs a module-level "am I already running" flag --
shared mutable state -- to avoid overlapping with itself. If embedding is slow,
`index.vector.embed_batch_size` and `index.vector.threads` are the knobs.

A failure here is recorded, not raised: the cycle continues to its hooks, and
`IndexOutcome.errors` makes the manifest's verdict false.
"""

from __future__ import annotations

import time
from datetime import datetime

import structlog

from m365_brain.config.index import IndexConfig
from m365_brain.index.backends import create_index_backend
from m365_brain.index.sync import sync_index
from m365_brain.index.vector import create_embedding_provider, create_vector_store, sync_vectors
from m365_brain.manifest import IndexOutcome
from m365_brain.schedule import INDEX_UNIT, mark_failure, mark_success
from m365_brain.state import StateStore

log = structlog.get_logger()


def run_index_step(index: IndexConfig, state: StateStore, now: datetime, full_rebuild: bool) -> IndexOutcome:
    """Sync the index and, when enabled, the vectors. Never raises."""
    started = time.monotonic()
    roots = [root.name for root in index.roots]
    backend = create_index_backend(index)
    try:
        backend.initialize()
        stats = sync_index(index, backend, full_rebuild)
        if index.vector.enabled:
            sync_vectors(index.vector, backend, create_embedding_provider(index), create_vector_store(index), False)
    except Exception as exc:  # noqa: BLE001 -- recorded on the manifest; hooks still fire
        log.exception("cycle.index_failed")
        mark_failure(state, INDEX_UNIT, now, str(exc) or type(exc).__name__)
        return _outcome(roots, indexed=0, skipped=0, pruned=0, errors=1, started=started)
    finally:
        backend.close()

    mark_success(state, INDEX_UNIT, now)
    return _outcome(
        roots,
        indexed=stats.indexed,
        skipped=stats.skipped,
        pruned=stats.pruned,
        errors=stats.errors,
        started=started,
    )


def _outcome(roots: list[str], *, indexed: int, skipped: int, pruned: int, errors: int, started: float) -> IndexOutcome:
    return IndexOutcome(
        roots=roots,
        indexed=indexed,
        skipped=skipped,
        pruned=pruned,
        errors=errors,
        elapsed_seconds=round(time.monotonic() - started, 3),
    )
