"""One cycle: extract, record, index, dispatch hooks. And the loop around it.

Scheduling (when does a unit run) lives in `schedule.py` and is pure.
Orchestration (what one cycle does) lives here and is all side effects. They
are separate modules because they have separate tests: the first is a function
of its arguments, the second needs a vault.

**Partial failure, stated once so no caller has to guess.**

| Failure                | Cycle continues | Cursor advances          | `ok`  |
|------------------------|-----------------|--------------------------|-------|
| one extractor raises   | yes, next unit  | `last_run_at` only       | False |
| every extractor raises | yes, to hooks   | `last_run_at` only       | False |
| the index step raises  | yes, hooks fire | n/a                      | False |
| one hook raises        | yes, later ones | n/a                      | False |
| config invalid         | no work at all  | n/a                      | n/a   |

Changes recorded before a mid-extractor failure are kept. They describe files
that exist on disk, and a hook never told about a written file is the exact
failure the manifest exists to remove.

**The manifest is written twice** -- once after the index step, before hooks
fire, and once after, with their outcomes filled in. A hook that takes the
process down must not take the record of what was extracted with it.

Two things today's daemon does that this does not: no background vector-sync
thread (a cycle that reports done while a thread is still writing is a cycle
that lies -- embedding happens inside the index step, and `index.vector` has
the knobs), and no proactive token-refresh unit (the transport already handles
401-refresh, and a second refresh path is an untested duplicate of a tested
one).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

import structlog

from m365_brain.config import Config, ConfigError, VaultConfig, require_section
from m365_brain.hooks import ResolvedHook, dispatch, resolve_hooks
from m365_brain.index_step import run_index_step
from m365_brain.m365.client import GraphClient
from m365_brain.manifest import (
    ChangeManifest,
    ChangeRecorder,
    ExtractorChanges,
    IndexOutcome,
    ManifestStore,
    RecordingStorage,
    new_cycle_id,
)
from m365_brain.schedule import INDEX_UNIT, Unit, due, mark_failure, mark_success, next_wake, read_cursors
from m365_brain.schedule import units_from_config as _units_from_config
from m365_brain.state import CYCLES, EXTRACTOR_STATE, StateStore
from m365_brain.storage.base import StorageBackend
from m365_brain.sync import build_context, run_one
from m365_brain.vault.paths import manifest_directory

log = structlog.get_logger()


@dataclass(frozen=True)
class Selection:
    """What this invocation was asked to run."""

    names: list[str] | None
    """`None` means every enabled unit; a list narrows it. An unrecognised name
    is a config error, never a silently empty run."""
    resync: bool
    """Forget the selected extractors' delta tokens first."""
    ignore_schedule: bool
    """`run --once` and `extract` run what was selected regardless of cursors;
    the continuous loop respects them."""


@dataclass(frozen=True)
class Runtime:
    """Everything a cycle needs, assembled once and reused across cycles."""

    config: Config
    vault: VaultConfig
    storage: StorageBackend
    state: StateStore
    manifests: ManifestStore
    token_provider: Callable[[], str]
    hooks: list[ResolvedHook]


def open_runtime(
    config: Config, storage: StorageBackend, state: StateStore, token_provider: Callable[[], str]
) -> Runtime:
    """Build a runtime, resolving hooks before any work can start.

    Hook resolution happens here rather than at first fire so a typo'd path
    fails in under a second at startup instead of four hours into a pass.
    """
    vault = require_section(config.vault, "vault")
    specs = config.hooks.post_cycle if config.hooks is not None else []
    return Runtime(
        config=config,
        vault=vault,
        storage=storage,
        state=state,
        manifests=ManifestStore(manifest_directory(vault), require_section(config.manifest, "manifest")),
        token_provider=token_provider,
        hooks=resolve_hooks(specs),
    )


def select_units(config: Config, names: Sequence[str] | None) -> list[Unit]:
    """The units this run may touch. Raises rather than running nothing."""
    units = _units_from_config(config)
    if names is None:
        chosen = units
    else:
        known = {unit.name: unit for unit in units}
        unknown = [name for name in names if name not in known]
        if unknown:
            raise ConfigError(f"unknown or disabled unit(s): {sorted(unknown)}; enabled units are {sorted(known)}")
        chosen = [known[name] for name in names]
    if not chosen:
        raise ConfigError(
            "no units selected -- every extractor is disabled and there is no index section. "
            "A run that can do nothing is a configuration bug, not an empty success."
        )
    return chosen


def run_once(runtime: Runtime, selection: Selection) -> ChangeManifest:
    """One cycle. Always returns a manifest, even when everything failed."""
    started = datetime.now(UTC)
    units = select_units(runtime.config, selection.names)
    if selection.resync:
        _forget_state(runtime, units)

    running = units if selection.ignore_schedule else due(units, read_cursors(runtime.state, units), started)
    extractor_units = [unit for unit in running if unit.name != INDEX_UNIT]
    log.info("cycle.start", units=[unit.name for unit in running])

    results = _run_extractors(runtime, extractor_units)
    index = _maybe_index(runtime, units, running, results, started)

    manifest = ChangeManifest(
        cycle_id=new_cycle_id(started),
        started_at=started,
        finished_at=datetime.now(UTC),
        extractors=results,
        index=index,
        hooks=[],
    )
    runtime.manifests.write(manifest)

    manifest = manifest.model_copy(update={"hooks": dispatch(runtime.hooks, manifest)})
    runtime.manifests.write(manifest)
    runtime.manifests.prune()
    _record_summary(runtime, manifest)
    log.info("cycle.done", cycle_id=manifest.cycle_id, ok=manifest.ok, failures=manifest.failures())
    return manifest


def run_forever(runtime: Runtime, selection: Selection, delay_seconds: int) -> int:
    """`run_once` on a schedule until a unit's failure streak exceeds the cap.

    Returns a process exit code. The broad `except Exception` is the second and
    last one in this package: a loop that dies on an unclassified error is a
    daemon that stops syncing silently, and the alternative -- enumerating
    every exception eight extractors and an index can raise -- is a list that
    is wrong the day it is written.
    """
    ceiling = runtime.config.service.max_consecutive_auth_failures
    slice_seconds = runtime.config.service.continuous_poll_seconds
    if delay_seconds:
        log.info("cycle.delayed_start", seconds=delay_seconds)
        time.sleep(delay_seconds)

    while True:
        try:
            run_once(runtime, selection)
        except ConfigError:
            raise
        except Exception:  # noqa: BLE001 -- see the docstring
            log.exception("cycle.failed")

        exhausted = _exhausted_units(runtime, selection, ceiling)
        if exhausted:
            log.error("cycle.failure_ceiling_reached", units=exhausted, ceiling=ceiling)
            return 1

        units = select_units(runtime.config, selection.names)
        now = datetime.now(UTC)
        wake = next_wake(units, read_cursors(runtime.state, units), now)
        _sleep_until(wake, slice_seconds)


def _run_extractors(runtime: Runtime, units: Sequence[Unit]) -> list[ExtractorChanges]:
    if not units:
        return []
    results: list[ExtractorChanges] = []
    with GraphClient(runtime.config.graph, runtime.token_provider) as client:
        for unit in units:
            results.append(_run_extractor(runtime, client, unit))
    return results


def _run_extractor(runtime: Runtime, client: GraphClient, unit: Unit) -> ExtractorChanges:
    started = datetime.now(UTC)
    recorder = ChangeRecorder()
    storage = RecordingStorage(runtime.storage, recorder)
    ctx = build_context(runtime.config, storage, recorder)
    state = runtime.state.get(EXTRACTOR_STATE, unit.name)

    error: str | None = None
    count = 0
    try:
        updated, count = run_one(runtime.config, client, storage, ctx, state, unit.name)
    except Exception as exc:  # noqa: BLE001 -- recorded on the manifest, next unit still runs
        error = str(exc) or type(exc).__name__
        log.exception("cycle.extractor_failed", extractor=unit.name)
        mark_failure(runtime.state, unit.name, datetime.now(UTC), error)
    else:
        runtime.state.put(EXTRACTOR_STATE, unit.name, updated)
        mark_success(runtime.state, unit.name, datetime.now(UTC))

    return ExtractorChanges(
        name=unit.name,
        started_at=started,
        finished_at=datetime.now(UTC),
        item_count=count,
        changes=recorder.changes(),
        error=error,
    )


def _maybe_index(
    runtime: Runtime, units: Sequence[Unit], running: Sequence[Unit], results: Sequence[ExtractorChanges], now: datetime
) -> IndexOutcome | None:
    """Index when this cycle changed something, or when the index unit is due.

    The second half is what makes a root nobody extracts into first-class: it
    is indexed on its own interval rather than as a side effect of extraction.
    """
    if runtime.config.index is None or not any(unit.name == INDEX_UNIT for unit in units):
        return None
    scheduled = any(unit.name == INDEX_UNIT for unit in running)
    if not scheduled and not any(entry.changes for entry in results):
        return None
    return run_index_step(require_section(runtime.config.index, "index"), runtime.state, now, False)


def _forget_state(runtime: Runtime, units: Sequence[Unit]) -> None:
    for unit in units:
        if unit.name == INDEX_UNIT:
            continue
        runtime.state.delete(EXTRACTOR_STATE, unit.name)
        log.warning("cycle.state_cleared", extractor=unit.name)


def _record_summary(runtime: Runtime, manifest: ChangeManifest) -> None:
    runtime.state.put(
        CYCLES,
        manifest.cycle_id,
        {
            "finished_at": manifest.finished_at.isoformat(),
            "ok": manifest.ok,
            "extractors": [entry.name for entry in manifest.extractors],
            "changes": len(manifest.paths(kind=None, extractor=None)),
            "failures": manifest.failures(),
        },
    )


def _exhausted_units(runtime: Runtime, selection: Selection, ceiling: int) -> list[str]:
    units = select_units(runtime.config, selection.names)
    cursors = read_cursors(runtime.state, units)
    return sorted(name for name, cursor in cursors.items() if int(cursor.get("consecutive_failures", 0)) > ceiling)


def _sleep_until(wake: datetime, slice_seconds: int) -> None:
    """Sleep in bounded slices so a SIGTERM is answered promptly."""
    while True:
        remaining = (wake - datetime.now(UTC)).total_seconds()
        if remaining <= 0:
            return
        time.sleep(min(remaining, slice_seconds))
