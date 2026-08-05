"""`IntentStore` over `StorageBackend` + `VaultPaths`.

Every path comes from the resolver, so the same store works against a local
directory and a blob container without a branch, and no directory name appears
here as a literal.

**The claim is not atomic, and that is a named ceiling rather than an
oversight.** The design this follows claims by `os.rename`, which is atomic on
POSIX and therefore doubles as a lock. `StorageBackend` has no rename -- it is
`write`/`read`/`delete` over both a filesystem and a blob container -- so the
claim here is: refuse if something is already in flight, read, write in flight,
delete the source. A second runner that starts after the in-flight write is
refused; one that starts inside the read-to-write window is not.

# ponytail: single-runner claim. Upgrade path is one atomic `move` on
# StorageBackend (rename locally, server-side copy+delete on blob), after which
# this becomes a one-line claim and the window closes.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import structlog

from m365_brain.outbox.stores import IntentAlreadyClaimed, IntentNotClaimed
from m365_brain.storage.base import StorageBackend
from m365_brain.vault.classify import MARKDOWN_SUFFIX, PathClassification, classify_outbox_path
from m365_brain.vault.dispatch import DispatchReceipt
from m365_brain.vault.intent import IntentEnvelope, parse_intent
from m365_brain.vault.paths import RECEIPT_SUFFIX, VaultPaths

log = structlog.get_logger()


def _stem(key: str, suffix: str) -> str:
    return key.rsplit("/", 1)[-1].removesuffix(suffix)


class FilesystemIntentStore:
    """Intents on a storage backend, archived under the configured meta tree."""

    def __init__(self, storage: StorageBackend, paths: VaultPaths, outbox_names: tuple[str, ...]) -> None:
        self._storage = storage
        self._paths = paths
        self._outbox_names = outbox_names

    def put(self, outbox_name: str, uuid: str, content: str) -> None:
        if outbox_name not in self._outbox_names:
            raise KeyError(f"no outbox named {outbox_name!r}; configured: {sorted(self._outbox_names)}")
        self._storage.write_file(self._paths.outbox_intent(outbox_name, uuid), content)

    def pending(self) -> Iterator[tuple[str, str]]:
        layout = self._paths.vault.layout
        for outbox_name in self._outbox_names:
            for key in self._storage.list_files(self._paths.outbox(outbox_name)):
                verdict = classify_outbox_path(key, layout)
                if verdict.classification is not PathClassification.VALID:
                    log.warning(
                        "outbox.path_ignored",
                        path=key,
                        classification=verdict.classification.value,
                        reason=verdict.reason,
                    )
                    continue
                yield verdict.outbox_name, verdict.uuid

    def claim(self, outbox_name: str, uuid: str) -> IntentEnvelope:
        source = self._paths.outbox_intent(outbox_name, uuid)
        inflight = self._paths.inflight(uuid)
        if self._storage.file_exists(inflight):
            raise IntentAlreadyClaimed(f"{uuid} is already in flight at {inflight}")
        if not self._storage.file_exists(source):
            raise IntentAlreadyClaimed(f"{source} is gone; another runner claimed it")
        content = self._storage.read_file(source)
        self._storage.write_file(inflight, content)
        self._storage.delete_file(source)
        return parse_intent(content, source, uuid)

    def already_dispatched(self, uuid: str) -> bool:
        return self._storage.file_exists(self._paths.processed(uuid)) or self._storage.file_exists(
            self._paths.rejected(uuid)
        )

    def archive(self, uuid: str, receipt: DispatchReceipt) -> None:
        inflight = self._paths.inflight(uuid)
        if not self._storage.file_exists(inflight):
            raise IntentNotClaimed(f"{uuid} is not in flight; claim it before archiving")
        dispatched = receipt.outcome == "dispatched"
        target = self._paths.processed(uuid) if dispatched else self._paths.rejected(uuid)
        sidecar = self._paths.processed_receipt(uuid) if dispatched else self._paths.rejected_receipt(uuid)
        self._storage.write_file(target, self._storage.read_file(inflight))
        self._storage.write_file(sidecar, receipt.model_dump_json(indent=2))
        self._storage.delete_file(inflight)

    def inflight(self) -> list[str]:
        root = self._paths.meta(self._paths.vault.layout.inflight)
        return sorted(_stem(key, MARKDOWN_SUFFIX) for key in self._storage.list_files(root))

    def receipt(self, uuid: str) -> DispatchReceipt | None:
        for key in (self._paths.processed_receipt(uuid), self._paths.rejected_receipt(uuid)):
            if self._storage.file_exists(key):
                return DispatchReceipt.model_validate(json.loads(self._storage.read_file(key)))
        return None

    def dispatched_receipts(self) -> Iterator[DispatchReceipt]:
        root = self._paths.meta(self._paths.vault.layout.processed)
        for key in sorted(self._storage.list_files(root)):
            if not key.endswith(RECEIPT_SUFFIX):
                continue
            receipt = DispatchReceipt.model_validate(json.loads(self._storage.read_file(key)))
            if receipt.outcome == "dispatched":
                yield receipt

    def archived_intent(self, uuid: str) -> IntentEnvelope | None:
        for key in (self._paths.processed(uuid), self._paths.rejected(uuid)):
            if self._storage.file_exists(key):
                return parse_intent(self._storage.read_file(key), key, uuid)
        return None

    def reconciled_verdict(self, uuid: str) -> str | None:
        key = self._paths.reconciled(uuid)
        if not self._storage.file_exists(key):
            return None
        return str(json.loads(self._storage.read_file(key))["verdict"])

    def mark_reconciled(self, uuid: str, verdict: str) -> None:
        self._storage.write_file(self._paths.reconciled(uuid), json.dumps({"verdict": verdict}))
