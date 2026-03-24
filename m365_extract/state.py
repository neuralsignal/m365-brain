"""Sync state manager. Persists delta tokens, timestamps, and counters per extractor."""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from pathlib import Path


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


class SyncState:
    """Read-modify-write access to sync state stored in a JSON file.

    Each extractor gets its own key in the top-level dict.
    """

    def __init__(self, path: str) -> None:
        self._path = Path(path)

    def load(self, key: str) -> dict:
        """Load state for a specific extractor. Returns {} if no state exists."""
        return self._read().get(key, {})

    def save(self, key: str, state: dict) -> None:
        """Persist state for a specific extractor (merge into existing file)."""
        data = self._read()
        data[key] = state
        self._write(data)

    def clear(self, key: str) -> None:
        """Reset state for a specific extractor, forcing a full re-sync."""
        data = self._read()
        if key in data:
            data[key] = {}
            self._write(data)

    def _read(self) -> dict:
        if not self._path.exists():
            return {}
        return json.loads(self._path.read_text(encoding="utf-8"))

    def _write(self, data: dict) -> None:
        """Write state atomically via temp file + os.replace (POSIX atomic)."""
        _ensure_parent(self._path)
        fd, tmp_path = tempfile.mkstemp(dir=self._path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(json.dumps(data, indent=2))
            os.replace(tmp_path, self._path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)
            raise
