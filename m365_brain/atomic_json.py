"""Read and write a JSON file with no torn intermediate state.

Two consumers -- `state.py` and `manifest.py` -- and one invariant shared
between them: a reader must never observe a half-written file. They are peers
in the layer map and neither may import the other, so the invariant lives here
rather than being implemented twice and drifting once.

`mkstemp` in the *destination* directory plus `os.replace` is the POSIX-atomic
form. The temp file must share a filesystem with the destination for
`os.replace` to be atomic, which is the whole reason it is not written to the
system temp directory.

Absence is not an error. `read_json` returns `None` for a file that is not
there, because every caller here treats "nothing written yet" as a first run
rather than as a failure.

Concurrency ceiling: last writer wins, and there is no lock. One local process
owns a vault. If a second writer ever becomes real, the upgrade is a lock file
beside the destination, not a retry loop.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any


def read_json(path: Path) -> Any | None:
    """The file's parsed contents, or `None` when the file does not exist.

    A file that exists but does not parse raises: an unreadable state file is a
    real failure, and returning `None` for it would silently discard delta
    tokens and full-sync a mailbox.
    """
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    """Serialise `data` to `path`, atomically, creating parent directories."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(json.dumps(data, indent=2, sort_keys=True))
        os.replace(temp_name, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(temp_name)
        raise
