"""Propagating an upstream deletion into the vault.

The inbox mirrors upstream truth, so a deleted mail, a cancelled event or a
disabled account has to stop existing here too -- a vault that only ever grows
is a vault that lies. This is the one place that deletes inbox content.

The mechanism is an id -> storage-path map the extractor keeps in its own sync
state: without it there is no way back from an upstream id to the file that was
written for it, and the delete cannot happen at all. Two extractors already
kept such a map; the other six had to be given one, which is why removal is a
change to eight modules rather than one.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog

from m365_brain.storage.base import StorageBackend
from m365_brain.vault.paths import VaultPaths

log = structlog.get_logger()

PATH_MAP_STATE_KEY = "path_map"
"""Extractor sync-state key holding `{upstream id: storage path}`.

Every extractor write path fills it with a plain `path_map[id] = path`. That
line looks pointless in isolation, which is the hazard: omitting it does not
fail anything today, it fails a deletion months later, silently. The two file
extractors have always kept such a map under `file_paths`; the other six were
given one here, and that -- not the delete itself -- is why removal touched
eight modules.

Named in this module rather than in each extractor because `purge_extractor`
has to clear it and `CONTRACTS.md` has to document it; three copies of a magic
string is how one of them gets missed.
"""


@dataclass(frozen=True)
class RemovalHandler:
    """Deletes the vault file recorded for an upstream id."""

    storage: StorageBackend
    paths: VaultPaths

    def remove(self, *, extractor: str, upstream_id: str, path_map: dict[str, str]) -> bool:
        """Delete everything recorded for `upstream_id` and drop the map entry.

        Returns True iff a delete was issued. `path_map` is mutated in place.

        The recorded value is a *prefix*, not necessarily a single file. Half
        the extractors write an item as a directory -- an entry file plus
        `attachments/` and `attachments_converted/` beside it -- so deleting
        only the recorded markdown would leave the blobs behind under a
        directory nothing references any more. Both backends' `list_files` is
        prefix-based and returns a lone file unchanged, so one loop covers both
        shapes.

        Backends MUST treat `delete_file` as idempotent -- both current backends
        swallow a missing path, and this relies on it: upstream re-sends a
        `@removed` marker for an id it already sent one for, and a second pass
        must be a no-op rather than a 404.
        """
        storage_path = path_map.pop(upstream_id, None)
        if storage_path is None:
            return False
        targets = self.storage.list_files(storage_path)
        for target in targets:
            self.storage.delete_file(target)
        if not targets:
            # Nothing on disk: the vault was emptied, or a previous run crashed
            # between the delete and the state write. Idempotent, so still a
            # delete as far as the caller is concerned.
            self.storage.delete_file(storage_path)
        log.info(
            "vault.removal.deleted",
            extractor=extractor,
            upstream_id=upstream_id,
            path=storage_path,
            files_removed=len(targets),
        )
        return True


def purge_extractor(
    storage: StorageBackend,
    paths: VaultPaths,
    state: dict,
    extractor: str,
) -> int:
    """Remove one extractor's whole inbox subtree and its sync state.

    A verb, not a side effect of editing config: deleting a subtree because
    someone toggled a flag separates the decision from the consequence in time
    and gives the operator no confirmation step. Idempotent and re-runnable --
    a second call finds nothing and returns 0.
    """
    root = paths.inbox_root(extractor)
    removed = 0
    for storage_path in storage.list_files(root):
        storage.delete_file(storage_path)
        removed += 1
    state.clear()
    log.info("vault.purge.complete", extractor=extractor, root=root, files_removed=removed)
    return removed
