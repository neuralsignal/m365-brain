"""Everything the runtime remembers between processes, behind one protocol.

Three kinds of thing are remembered, and they are deliberately three
namespaces rather than three files with three formats:

| Namespace          | Key            | Value                                     |
|--------------------|----------------|-------------------------------------------|
| `EXTRACTOR_STATE`  | extractor name | delta tokens and watermarks               |
| `CURSORS`          | unit name      | when it last ran, last succeeded, failures |
| `CYCLES`           | cycle id       | one-line summary of a finished cycle      |

`last_run_at` and `last_success_at` are separate on purpose. Advancing
`last_run_at` on a failure is what stops a broken extractor from hot-looping
against a 500-ing endpoint; holding `last_success_at` back is what keeps the
staleness visible instead of silently absorbed. One timestamp cannot do both.

The namespace names are not config. They are the on-disk contract between a
process and its successor, not a layout an operator chooses -- and an operator
who renamed one would silently orphan every delta token in the vault.

Locking: none. One local process owns a vault; `atomic_json` makes each write
indivisible, and that is the whole concurrency story. If a second writer ever
becomes real the upgrade is a lock file per namespace, not a retry loop.

`InMemoryStateStore` ships in the library rather than in the tests because a
protocol with one implementation is a shape nobody has checked. Both run
against the same conformance suite.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Protocol, runtime_checkable

from m365_brain.atomic_json import read_json, write_json

EXTRACTOR_STATE = "extractor_state"
"""Delta tokens and watermarks -- what an extractor hands its next run."""

CURSORS = "cursors"
"""Per-unit scheduling bookkeeping. Written only by `schedule.py`."""

CYCLES = "cycles"
"""One summary row per finished cycle, for `status` and history."""


@runtime_checkable
class StateStore(Protocol):
    """Namespaced key/value persistence for small JSON documents."""

    def get(self, namespace: str, key: str) -> dict:
        """The stored document, or `{}` when there is none.

        Absence is not an error: "this extractor has never run" is the normal
        first-run condition, and raising for it would make every caller write
        the same try/except.
        """
        ...

    def put(self, namespace: str, key: str, value: dict) -> None:
        """Store a document, replacing whatever was there."""
        ...

    def delete(self, namespace: str, key: str) -> None:
        """Forget a key. Idempotent -- deleting an absent key is not an error."""
        ...

    def keys(self, namespace: str) -> list[str]:
        """Every key in a namespace, sorted. Empty for an unknown namespace."""
        ...


class JsonStateStore:
    """One JSON file per namespace under `root`, rewritten whole on each put.

    Whole-file rewrite rather than per-key files: a namespace holds tens of
    small documents, the write is atomic either way, and one file per key turns
    `keys()` into a directory listing whose contents depend on what else the
    operator dropped in there.
    """

    def __init__(self, root: Path) -> None:
        self._root = root

    def get(self, namespace: str, key: str) -> dict:
        return self._read(namespace).get(key, {})

    def put(self, namespace: str, key: str, value: dict) -> None:
        data = self._read(namespace)
        data[key] = value
        write_json(self._path(namespace), data)

    def delete(self, namespace: str, key: str) -> None:
        data = self._read(namespace)
        if key in data:
            del data[key]
            write_json(self._path(namespace), data)

    def keys(self, namespace: str) -> list[str]:
        return sorted(self._read(namespace))

    def _path(self, namespace: str) -> Path:
        return self._root / f"{namespace}.json"

    def _read(self, namespace: str) -> dict:
        data = read_json(self._path(namespace))
        if data is None:
            return {}
        if not isinstance(data, dict):
            raise ValueError(f"state namespace {namespace!r} is not a JSON object: {self._path(namespace)}")
        return data


class InMemoryStateStore:
    """Dicts, same semantics. The fake every runtime test runs against.

    Both `get` and `put` deep-copy. `JsonStateStore` reparses on every read, so
    a caller cannot reach back into what it stored -- and extractors do exactly
    that, mutating the state dict they were handed with `setdefault`. A fake
    that aliased its storage would let a *failed* extractor's mutations persist
    where the real store discards them, which is the class of divergence a fake
    exists to rule out rather than to introduce.
    """

    def __init__(self) -> None:
        self._data: dict[str, dict[str, dict]] = {}

    def get(self, namespace: str, key: str) -> dict:
        return deepcopy(self._data.get(namespace, {}).get(key, {}))

    def put(self, namespace: str, key: str, value: dict) -> None:
        self._data.setdefault(namespace, {})[key] = deepcopy(value)

    def delete(self, namespace: str, key: str) -> None:
        self._data.get(namespace, {}).pop(key, None)

    def keys(self, namespace: str) -> list[str]:
        return sorted(self._data.get(namespace, {}))
