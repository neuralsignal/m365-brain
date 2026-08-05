"""What one cycle wrote, as a value a consumer can act on.

The manifest replaces two things at once: consumers re-scanning the vault to
find out what changed, and consumers keeping their own watermark file to
remember what they had already seen. To replace a watermark it has to be a
*complete* record of the cycle's writes, not a summary -- so it is assembled by
wrapping the storage backend, and an extractor physically cannot write without
being recorded.

**Paths, not payloads.** A `FileChange` carries a path and nothing about the
file's contents. Embedding frontmatter here would be convenient for hooks and
would put Microsoft 365 vocabulary inside the one type every downstream
consumer imports. A hook that wants a sender address opens the file it was
handed; the scan is what was expensive, and the scan is gone.

**`record_ids`.** For an extractor that writes one file per upstream item the
path *is* the identity, and a path-level manifest is already a complete
watermark. For one that merges many records into a single file -- many Teams
messages, one rendered conversation, rewritten whole -- a path alone says only
"this changed", and a downstream trigger would have to re-read and re-match
every record every cycle, which is a watermark file by another name. So the two
Teams extractors volunteer the ids they merged in this pass. It is the one
thing in the manifest a producer must say out loud; everything else falls out
of `RecordingStorage`.

Persistence is the filesystem, not `StorageBackend`: the manifest is a fact
*about* a vault rather than content *in* it, and the state store already sits
beside it under the same meta directory. A blob-backed vault keeps its
manifests locally alongside its state -- if that ever needs to change, both
move together.
"""

from __future__ import annotations

import secrets
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, computed_field

from m365_brain.atomic_json import read_json, write_json
from m365_brain.config import ManifestConfig
from m365_brain.storage.base import StorageBackend

MANIFEST_MODEL_CONFIG = ConfigDict(frozen=True, extra="forbid")
"""Frozen and closed, but not `strict`: a manifest is read back from JSON, and
strict mode would refuse to parse its own ISO timestamps into `datetime`."""

ChangeKind = Literal["added", "updated", "removed"]


class FileChange(BaseModel):
    """One file this cycle wrote or removed."""

    model_config = MANIFEST_MODEL_CONFIG
    path: str
    """Vault-relative, POSIX separators -- the same key the storage backend took."""
    kind: ChangeKind
    record_ids: list[str]
    """Ids merged into this file in this pass. Empty unless the file holds many
    records; see the module docstring."""


class ExtractorChanges(BaseModel):
    """One extractor's contribution to a cycle."""

    model_config = MANIFEST_MODEL_CONFIG
    name: str
    started_at: datetime
    finished_at: datetime
    item_count: int
    changes: list[FileChange]
    error: str | None
    """`None` is success. A string is a failure, and never the empty string --
    an empty message would report a failure nobody can act on."""


class IndexOutcome(BaseModel):
    """What the index step did, when it ran."""

    model_config = MANIFEST_MODEL_CONFIG
    roots: list[str]
    indexed: int
    skipped: int
    pruned: int
    errors: int
    elapsed_seconds: float


class HookOutcome(BaseModel):
    """One post-cycle hook, and whether it raised."""

    model_config = MANIFEST_MODEL_CONFIG
    spec: str
    error: str | None


class ChangeManifest(BaseModel):
    """The record of one cycle. The value hooks receive and consumers read."""

    model_config = MANIFEST_MODEL_CONFIG
    cycle_id: str
    started_at: datetime
    finished_at: datetime
    extractors: list[ExtractorChanges]
    index: IndexOutcome | None
    """`None` when the index step did not run this cycle."""
    hooks: list[HookOutcome]

    @computed_field
    @property
    def ok(self) -> bool:
        """True only when nothing failed anywhere.

        A hook failure degrades the verdict exactly like an extractor failure.
        That is the whole difference between fail-soft and swallowed: the cycle
        carries on, and nothing about the outcome claims success.

        A computed *field*, not a bare property: the persisted manifest and the
        `--json` output are the same document, and a verdict a consumer has to
        recompute from three nested lists is a verdict two consumers will
        recompute differently.
        """
        return not self.failures()

    def paths(self, *, kind: ChangeKind | None, extractor: str | None) -> list[str]:
        """Changed paths, optionally narrowed. `None` for either means "any"."""
        return [
            change.path
            for entry in self.extractors
            if extractor is None or entry.name == extractor
            for change in entry.changes
            if kind is None or change.kind == kind
        ]

    def failures(self) -> list[str]:
        """One human-readable line per failure, in the order they happened."""
        lines = [f"extractor {entry.name}: {entry.error}" for entry in self.extractors if entry.error is not None]
        if self.index is not None and self.index.errors:
            lines.append(f"index: {self.index.errors} file(s) failed to index")
        lines.extend(f"hook {outcome.spec}: {outcome.error}" for outcome in self.hooks if outcome.error is not None)
        return lines


def new_cycle_id(now: datetime) -> str:
    """`20260805T101503Z-a3f1c2` -- sortable by time, unique within a second."""
    return f"{now.strftime('%Y%m%dT%H%M%SZ')}-{secrets.token_hex(3)}"


class ChangeRecorder:
    """Accumulates one extractor's writes. Handed to `RecordingStorage`."""

    def __init__(self) -> None:
        self._kinds: dict[str, ChangeKind] = {}
        self._records: dict[str, list[str]] = {}

    def record(self, path: str, kind: ChangeKind) -> None:
        """Note a write or a delete.

        A file written twice in one pass appears once, and stays `added`: from
        a consumer's point of view it is new to this cycle either way. A later
        `removed` always wins, and a write after a remove wins back.
        """
        if self._kinds.get(path) == "added" and kind == "updated":
            return
        self._kinds[path] = kind

    def note_records(self, path: str, record_ids: Sequence[str]) -> None:
        """Declare which upstream records were merged into `path` this pass."""
        self._records.setdefault(path, []).extend(record_ids)

    def changes(self) -> list[FileChange]:
        """Every change, path-sorted.

        Ids noted for a path that was never written are dropped: nothing
        changed, so there is nothing for a consumer to react to.
        """
        return [
            FileChange(path=path, kind=kind, record_ids=sorted(set(self._records.get(path, []))))
            for path, kind in sorted(self._kinds.items())
        ]


class RecordingStorage:
    """A `StorageBackend` that reports every write and delete to a recorder.

    Wrapping the backend rather than changing eight extractor signatures is not
    only the smaller diff -- it is the one that cannot drift. An extractor has
    no way to write bytes into the vault except through this object, so a new
    write path is recorded the day it is added rather than the day somebody
    remembers to declare it.
    """

    def __init__(self, inner: StorageBackend, recorder: ChangeRecorder) -> None:
        self._inner = inner
        self._recorder = recorder

    def write_file(self, path: str, content: str) -> None:
        existed = self._inner.file_exists(path)
        self._inner.write_file(path, content)
        self._recorder.record(path, "updated" if existed else "added")

    def write_bytes(self, path: str, content: bytes) -> None:
        existed = self._inner.file_exists(path)
        self._inner.write_bytes(path, content)
        self._recorder.record(path, "updated" if existed else "added")

    def delete_file(self, path: str) -> None:
        self._inner.delete_file(path)
        self._recorder.record(path, "removed")

    def read_file(self, path: str) -> str:
        return self._inner.read_file(path)

    def file_exists(self, path: str) -> bool:
        return self._inner.file_exists(path)

    def list_files(self, prefix: str) -> list[str]:
        return self._inner.list_files(prefix)


COMPUTED_FIELDS = ("ok",)
"""Serialised for consumers, dropped on the way back in.

A computed field is output-only; `extra="forbid"` would reject it as an unknown
key on re-validation. Popping it here keeps the model closed -- the alternative,
`extra="ignore"`, would also swallow a genuinely corrupt manifest."""


def _parse(document: object) -> ChangeManifest | None:
    if document is None:
        return None
    if not isinstance(document, dict):
        raise ValueError(f"a manifest must be a JSON object, got {type(document).__name__}")
    return ChangeManifest.model_validate({k: v for k, v in document.items() if k not in COMPUTED_FIELDS})


class ManifestStore:
    """Cycle manifests on disk, newest also copied to the pointer file."""

    def __init__(self, directory: Path, config: ManifestConfig) -> None:
        self._directory = directory
        self._config = config

    def write(self, manifest: ChangeManifest) -> Path:
        """Persist a manifest and refresh the pointer file. Idempotent.

        Called twice per cycle -- once before hooks fire and once after, with
        their outcomes filled in. A hook that takes the process down with it
        must not take the record of what was extracted along.
        """
        document = manifest.model_dump(mode="json")
        path = self._directory / f"{manifest.cycle_id}.json"
        write_json(path, document)
        write_json(self._directory / self._config.latest_filename, document)
        return path

    def read(self, cycle_id: str) -> ChangeManifest | None:
        """One manifest by id, or `None` when it has been pruned."""
        return _parse(read_json(self._directory / f"{cycle_id}.json"))

    def latest(self) -> ChangeManifest | None:
        """The most recent manifest, or `None` before the first cycle."""
        return _parse(read_json(self._directory / self._config.latest_filename))

    def cycle_ids(self) -> list[str]:
        """Every retained cycle id, oldest first. The ids sort chronologically."""
        if not self._directory.is_dir():
            return []
        latest = self._config.latest_filename
        return sorted(p.stem for p in self._directory.glob("*.json") if p.name != latest)

    def prune(self) -> list[str]:
        """Delete all but the newest `manifest.retain_cycles`. Returns what went."""
        ids = self.cycle_ids()
        doomed = ids[: max(0, len(ids) - self._config.retain_cycles)]
        for cycle_id in doomed:
            (self._directory / f"{cycle_id}.json").unlink()
        return doomed
