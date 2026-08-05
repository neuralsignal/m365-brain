"""Checksum-driven incremental sync over the configured roots.

Files are the source of truth and the index is derived: a deletion is set
arithmetic on the scan result, not a filesystem event, so a missed change is
corrected by the next run and `full_rebuild=True` is always available.

The phase order is load-bearing and is not an implementation detail:

1. **scan** every root, checksum, compare -- no writes at all
2. **prune** entities whose files are gone, *before any insert*. A renamed file
   frees its permalink first; inserting first collides with a permalink the
   departing entity still owns.
3. **load permalink owners** *after* the prune, in one bulk read, so every
   conflict check is a dict lookup rather than a query per entity.
4. **parse and write** in batches, one short transaction each
5. **resolve** forward references, then **rebuild** the text index

Writes are batched so the store's write lock is released between batches: a
single sync-long transaction is a lock-contention generator for anything else
touching the same file.
"""

from __future__ import annotations

import fnmatch
import time
from dataclasses import replace
from pathlib import Path, PurePosixPath

import structlog

from m365_brain.config.index import IndexConfig, IndexRoot
from m365_brain.index.backends.base import IndexBackend
from m365_brain.model import Entity, SyncStats
from m365_brain.parsers.document import parse_markdown_file
from m365_brain.parsers.text import file_checksum, slugify

log = structlog.get_logger()


def sync_index(config: IndexConfig, backend: IndexBackend, full_rebuild: bool) -> SyncStats:
    """Bring the index in line with the files under `config.roots`."""
    started = time.monotonic()
    indexed_before = backend.indexed_files()

    found_keys: set[str] = set()
    to_parse: list[tuple[IndexRoot, Path, str]] = []
    total = 0
    skipped = 0
    errors = 0

    for root in config.roots:
        for path, relative in _discover(root, config):
            total += 1
            key = f"{root.name}/{relative}"
            found_keys.add(key)
            known = indexed_before.get(key)
            if not full_rebuild and known is not None:
                try:
                    checksum = file_checksum(path)
                except OSError:
                    errors += 1
                    continue
                if checksum == known.checksum:
                    skipped += 1
                    continue
            to_parse.append((root, path, key))

    pruned = backend.delete_entities(sorted(set(indexed_before) - found_keys))
    owners = backend.permalink_owners()

    indexed = 0
    batch: list[Entity] = []
    for root, path, _key in to_parse:
        entity = parse_markdown_file(path, root, config)
        if entity is None:
            errors += 1
            continue
        batch.append(_ensure_unique_permalink(entity, owners))
        if len(batch) >= config.sync.batch_size:
            backend.upsert_entities(batch)
            indexed += len(batch)
            batch = []
    if batch:
        backend.upsert_entities(batch)
        indexed += len(batch)

    resolved = backend.resolve_relations()
    backend.rebuild_text_index()

    return SyncStats(
        total=total,
        indexed=indexed,
        skipped=skipped,
        pruned=pruned,
        resolved=resolved,
        errors=errors,
        elapsed_seconds=round(time.monotonic() - started, 3),
    )


def _discover(root: IndexRoot, config: IndexConfig) -> list[tuple[Path, str]]:
    """Every indexable file under one root, as `(path, root-relative posix path)`.

    A missing root is fatal and names the root: an empty result would look like
    an empty directory, and the next run would prune every entity that root owns.
    """
    root_path = Path(root.path)
    if not root_path.is_dir():
        raise FileNotFoundError(f"index root {root.name!r} does not exist: {root_path}")

    candidates = root_path.rglob("*") if root.recursive else root_path.glob("*")
    extensions = set(config.file_extensions)

    found: list[tuple[Path, str]] = []
    for path in sorted(candidates):
        if not path.is_file() or path.suffix not in extensions:
            continue
        relative = path.relative_to(root_path).as_posix()
        if _excluded(relative, config.exclude):
            continue
        found.append((path, relative))
    return found


def _excluded(relative: str, patterns: list[str]) -> bool:
    """Glob patterns against the root-relative path.

    `fnmatch` has no recursive `**`, and its `*` already crosses separators, so
    `**/x/**` matches a nested `x` but not one at the root. Retrying without the
    leading `**/` restores what every glob tool means by that pattern -- the
    alternative is that `["**/_meta/**"]` silently fails to exclude a top-level
    `_meta/`, which is exactly the case the pattern is usually written for.
    """
    for pattern in patterns:
        if fnmatch.fnmatch(relative, pattern):
            return True
        if pattern.startswith("**/") and fnmatch.fnmatch(relative, pattern[3:]):
            return True
    return False


def _ensure_unique_permalink(entity: Entity, owners: dict[str, str]) -> Entity:
    """Resolve a permalink already claimed by a different entity.

    The fallback is derived from the entity key, which is unique by
    construction, so the replacement cannot collide in turn. `owners` is updated
    in place and therefore tracks both stored and in-flight permalinks without a
    single extra query.
    """
    claimed_by = owners.get(entity.permalink)
    if claimed_by is None or claimed_by == entity.key:
        owners[entity.permalink] = entity.key
        return entity

    fallback = slugify(PurePosixPath(entity.key).with_suffix("").as_posix().replace("/", "-"))
    log.warning(
        "index.sync.permalink_conflict_resolved",
        original_permalink=entity.permalink,
        resolved_permalink=fallback,
        entity_key=entity.key,
        conflicting_key=claimed_by,
    )
    owners[fallback] = entity.key
    return replace(entity, permalink=fallback)
