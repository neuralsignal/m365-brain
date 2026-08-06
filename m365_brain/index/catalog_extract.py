"""The conversion pipeline: `initial -> converted | failed`, one batch at a time.

This module owns the state machine and nothing else. *How* a catalogued binary
becomes markdown is a `convert` callable the caller supplies, for two reasons:
the converters live under `m365/`, which is this layer's peer and therefore
unimportable, and a caller that wants a different converter should not have to
fork the loop that records the outcome.

**Failures are recorded, then skipped.** A row that failed stays failed and the
next run does not touch it -- otherwise a single unconvertible PDF is retried
on every invocation forever, and the batch fills with work that has already
been proven not to succeed. `retry_failed` is the deliberate escape hatch: it
puts the failed rows back in the batch when a caller has fixed the reason.

`convert` raises `CatalogConversionError` and nothing else. A bare
`except Exception` here would be this package's third, and this one has no
excuse: the caller knows exactly which exceptions its own converter throws, and
translating them at the boundary is where that knowledge already is.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from m365_brain.config.index import CatalogConfig
from m365_brain.index.catalog import FileCatalog
from m365_brain.model import CatalogEntry, CatalogQuery


class CatalogConversionError(Exception):
    """A conversion that failed for a reason worth recording on the row."""


@dataclass(frozen=True, slots=True)
class ExtractStats:
    """What one `extract` pass did, against what there was to do.

    `attempted == converted + failed`, and `attempted < eligible` means the
    limit stopped the pass with work still queued. Without `eligible` a caller
    could not tell a pass that finished the backlog from one that took the
    first hundred rows of it -- both print the same counters.
    """

    eligible: int
    attempted: int
    converted: int
    failed: int


def pending_batch(catalog: FileCatalog, config: CatalogConfig, limit: int, retry_failed: bool) -> list[CatalogEntry]:
    """The rows the next pass would convert, oldest-modified last.

    Failed rows are appended after the pending ones rather than merged: work
    that has never been tried comes before work that is being retried, so a
    small `limit` still makes progress on the backlog.
    """
    entries = _search(catalog, config.initial_state, limit)
    if retry_failed and len(entries) < limit:
        entries += _search(catalog, config.failed_state, limit - len(entries))
    return entries


def pending_total(catalog: FileCatalog, config: CatalogConfig, retry_failed: bool) -> int:
    """How many rows a pass would take if `limit` did not cap it."""
    eligible = _count(catalog, config.initial_state)
    if retry_failed:
        eligible += _count(catalog, config.failed_state)
    return eligible


def extract_pending(
    catalog: FileCatalog,
    config: CatalogConfig,
    convert: Callable[[CatalogEntry], str],
    limit: int,
    retry_failed: bool,
) -> ExtractStats:
    """Convert one batch, recording every outcome. Re-runnable by construction."""
    converted = 0
    failed = 0
    eligible = pending_total(catalog, config, retry_failed)
    entries = pending_batch(catalog, config, limit, retry_failed)
    for entry in entries:
        try:
            output_path = convert(entry)
        except CatalogConversionError as exc:
            catalog.set_status(entry.original_path, config.failed_state, None, str(exc))
            failed += 1
        else:
            catalog.set_status(entry.original_path, config.converted_state, output_path, None)
            converted += 1
    return ExtractStats(eligible=eligible, attempted=len(entries), converted=converted, failed=failed)


def converted_output_path(original_path: str, attachments_dir: str, converted_dir: str) -> str:
    """Where the markdown for a catalogued binary belongs.

    The same path the eager converters already write to, derived rather than
    duplicated: a binary at `<item>/<attachments>/<rest>` converts to
    `<item>/<converted>/<rest>.md`. Both directory names come from
    `vault.filenames`, so a renamed layout moves the output with it.

    Raises on a path outside an attachments directory. Every binary this
    package writes today lands in one, so an exception here means a new write
    path arrived without a home for its markdown -- which is worth a crash
    rather than a file dropped somewhere nothing looks.
    """
    segments = original_path.split("/")
    try:
        position = segments.index(attachments_dir)
    except ValueError:
        raise ValueError(
            f"{original_path!r} is not under a {attachments_dir!r} directory, so there is nowhere "
            f"to put its markdown; extend converted_output_path before cataloguing writes elsewhere"
        ) from None
    segments[position] = converted_dir
    return "/".join(segments) + ".md"


def _search(catalog: FileCatalog, status: str, limit: int) -> list[CatalogEntry]:
    return catalog.search(_in_state(status, limit))


def _count(catalog: FileCatalog, status: str) -> int:
    """`limit` is required by the query and ignored by the count."""
    return catalog.count(_in_state(status, limit=0))


def _in_state(status: str, limit: int) -> CatalogQuery:
    return CatalogQuery(
        extension=None,
        extractor=None,
        status=status,
        modified_after=None,
        name_contains=None,
        limit=limit,
    )
