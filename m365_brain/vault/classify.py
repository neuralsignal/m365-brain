"""Verdicts on untrusted outbox paths.

Separate from `paths` because the direction is opposite: `paths` *builds* keys
from config and raises on bad input, this *judges* keys that arrived from
somewhere else and never raises. A classifier that throws is a classifier a
caller has to wrap in a try/except and turn back into a verdict, so it returns
one directly.

Valid shape is `<outbox>/<outbox name>/<uuid>.md`. The archive segments are
`SKIP` -- already dealt with. Everything else is `REJECT`, with a reason string
the caller records on the rejection so an operator learns what was wrong with
the file rather than that something was.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from m365_brain.config import VaultLayout

MARKDOWN_SUFFIX = ".md"


class PathClassification(StrEnum):
    """What to do with a path found under the outbox root."""

    VALID = "valid"
    SKIP = "skip"
    REJECT = "reject"


@dataclass(frozen=True)
class ClassifiedPath:
    """A verdict. `outbox_name` and `uuid` are set only when VALID; `reason`
    only when REJECT."""

    classification: PathClassification
    outbox_name: str | None
    uuid: str | None
    reason: str | None


def _reject(reason: str) -> ClassifiedPath:
    return ClassifiedPath(PathClassification.REJECT, None, None, reason)


def classify_outbox_path(path: str, layout: VaultLayout) -> ClassifiedPath:
    """Classify one storage key under the outbox root. Never raises.

    The archive segment names, like the outbox root itself, come from `layout`
    -- a hardcoded `_processed` here would silently stop skipping the archive
    the moment an operator renamed it, and the runner would re-dispatch every
    intent it had ever sent.
    """
    if not isinstance(path, str) or not path:
        return _reject("empty or non-string path")
    if path.startswith("/") or path.startswith("\\"):
        return _reject(f"path is absolute: {path!r}")

    normalised = path.replace("\\", "/")
    if ".." in normalised.split("/"):
        return _reject(f"path contains traversal: {path!r}")

    segments = [segment for segment in normalised.split("/") if segment]
    root = layout.outbox
    if not segments or segments[0] != root:
        return _reject(f"path is not under {root!r}: {path!r}")

    tail = segments[1:]
    archives = {layout.processed, layout.rejected, layout.inflight}
    if archives & set(tail):
        return ClassifiedPath(PathClassification.SKIP, None, None, None)

    shape = f"{root}/{{outbox_name}}/<uuid>{MARKDOWN_SUFFIX}"
    if len(tail) != 2:
        return _reject(f"path does not match outbox layout {shape}: {path!r}")

    outbox_name, filename = tail
    if not filename.endswith(MARKDOWN_SUFFIX):
        return _reject(f"intent filename must end with {MARKDOWN_SUFFIX}: {path!r}")

    uuid = filename[: -len(MARKDOWN_SUFFIX)]
    if not uuid:
        return _reject(f"intent filename is empty: {path!r}")

    return ClassifiedPath(PathClassification.VALID, outbox_name, uuid, None)
