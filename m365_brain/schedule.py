"""When a unit is next due, computed from persisted cursors.

Two pure functions and the cursor bookkeeping they read. Nothing here runs
anything, catches anything, or sleeps -- `due()` answers a question about the
present and `next_wake()` answers one about the future, and both are total
functions of their arguments. The loop that acts on the answers lives in
`cycle.py`, where the side effects are.

**Why the cursors are persisted rather than kept in memory.** A scheduler that
counts intervals from process start restarts its clock every time the process
does, so a daemon restarted hourly with a six-hour SharePoint interval never
runs SharePoint at all. That is not a hypothetical -- it is the behaviour this
module replaces, and the fix is thirty lines and one fewer dependency.

**The index is a unit.** Not a step that happens to follow extraction: a
configured root that no extractor writes into still has to be indexed, and
giving the index its own interval is what makes that literally true rather
than a special case in the cycle.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from m365_brain.config import EXTRACTOR_NAMES, Config
from m365_brain.state import CURSORS, StateStore

INDEX_UNIT = "index"
"""The index's name as a schedule unit. Not config: `vault.extractor_dirs`
already rejects an extractor this package does not implement, so the name space
is closed and this cannot collide with one of the eight."""

SECONDS_PER_MINUTE = 60


@dataclass(frozen=True)
class Unit:
    """Something that runs on an interval. An extractor, or the index."""

    name: str
    interval_seconds: int
    backoff_base_seconds: int
    """Wait after the first consecutive failure; doubles with each further one."""
    backoff_max_seconds: int
    """Ceiling on that wait. The unit's own interval is the floor."""


def units_from_config(config: Config) -> list[Unit]:
    """Every enabled extractor, plus the index. Config order, then the index.

    Disabled extractors are absent rather than present-with-a-flag: a unit that
    exists but never runs still shows up in `status`, and a unit nobody can
    schedule is not a unit.
    """
    extractors = config.extractors
    base = config.service.unit_backoff_base_seconds
    cap = config.service.unit_backoff_max_seconds
    intervals = {
        name: getattr(extractors, name).poll_interval_minutes
        for name in EXTRACTOR_NAMES
        if getattr(extractors, name).enabled
    }
    if config.index is not None:
        intervals[INDEX_UNIT] = config.index.sync.interval_minutes
    return [
        Unit(
            name=name, interval_seconds=minutes * SECONDS_PER_MINUTE, backoff_base_seconds=base, backoff_max_seconds=cap
        )
        for name, minutes in intervals.items()
    ]


def wait_seconds(unit: Unit, consecutive_failures: int) -> int:
    """How long after its last run a unit is next due.

    The interval while healthy. After `n` consecutive failures,
    `base * 2**(n-1)` capped at `backoff_max_seconds`, and never shorter than
    the interval. A unit failing on a dead endpoint backs off on its own clock;
    every other unit keeps its schedule. The first success resets the streak,
    so recovery needs no restart.
    """
    if consecutive_failures <= 0:
        return unit.interval_seconds
    backoff = min(unit.backoff_max_seconds, unit.backoff_base_seconds * 2 ** (consecutive_failures - 1))
    return max(unit.interval_seconds, backoff)


def last_run(cursor: dict) -> datetime | None:
    """The cursor's `last_run_at` as an aware datetime. `None` before the first run."""
    stamp = cursor.get("last_run_at")
    return _parse(stamp) if stamp else None


def next_attempt(unit: Unit, cursor: dict) -> datetime | None:
    """When the unit is next due by its cursor. `None` before its first run.

    A unit whose last failure was an auth failure keeps its plain interval:
    backing it off would only delay the first sync after `auth login`.
    """
    ran = last_run(cursor)
    if ran is None:
        return None
    auth = int(cursor.get("consecutive_auth_failures", 0)) > 0
    streak = 0 if auth else int(cursor.get("consecutive_failures", 0))
    return ran + timedelta(seconds=wait_seconds(unit, streak))


def due(units: Sequence[Unit], cursors: Mapping[str, dict], now: datetime) -> list[Unit]:
    """The units whose wait has elapsed, in the order they were given.

    A unit with no cursor, or a cursor with no `last_run_at`, is due: never
    having run is the strongest possible case for running.
    """
    return [unit for unit in units if _elapsed(unit, cursors, now) is None]


def next_wake(units: Sequence[Unit], cursors: Mapping[str, dict], now: datetime) -> datetime:
    """The earliest moment any unit becomes due. `now` when one already is.

    Never in the past, so a caller can sleep until it without checking.
    """
    waits = [_elapsed(unit, cursors, now) for unit in units]
    if not waits or any(wait is None for wait in waits):
        return now
    return now + min(wait for wait in waits if wait is not None)


def read_cursor(store: StateStore, name: str) -> dict:
    """One unit's cursor. `{}` before its first run."""
    return store.get(CURSORS, name)


def read_cursors(store: StateStore, units: Sequence[Unit]) -> dict[str, dict]:
    """Every named unit's cursor, in one read per unit."""
    return {unit.name: store.get(CURSORS, unit.name) for unit in units}


def mark_success(store: StateStore, name: str, now: datetime) -> None:
    """Both timestamps advance, and the failure streak resets."""
    stamp = _iso(now)
    store.put(
        CURSORS,
        name,
        {
            "last_run_at": stamp,
            "last_success_at": stamp,
            "consecutive_failures": 0,
            "consecutive_auth_failures": 0,
            "last_error": None,
        },
    )


def mark_failure(store: StateStore, name: str, now: datetime, error: str, auth: bool) -> None:
    """`last_run_at` advances; `last_success_at` deliberately does not.

    Advancing the run stamp is what stops a broken unit from hot-looping
    against an endpoint that is down. Holding the success stamp back is what
    keeps the staleness visible in `status` instead of silently absorbed.

    `auth` marks a failure only a human can fix (a login). Its own streak
    counts those alone and resets on any other outcome: it is what stops the
    daemon, while `consecutive_failures` only lengthens the unit's backoff.
    """
    previous = store.get(CURSORS, name)
    store.put(
        CURSORS,
        name,
        {
            "last_run_at": _iso(now),
            "last_success_at": previous.get("last_success_at"),
            "consecutive_failures": int(previous.get("consecutive_failures", 0)) + 1,
            "consecutive_auth_failures": int(previous.get("consecutive_auth_failures", 0)) + 1 if auth else 0,
            "last_error": error,
        },
    )


def _elapsed(unit: Unit, cursors: Mapping[str, dict], now: datetime) -> timedelta | None:
    """`None` when the unit is due, otherwise how long until it is."""
    attempt = next_attempt(unit, cursors.get(unit.name, {}))
    if attempt is None:
        return None
    remaining = attempt - now
    return None if remaining <= timedelta(0) else remaining


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(stamp: str) -> datetime:
    """Parse a cursor timestamp. A naive one is read as UTC, never as local.

    Reading a naive stamp as local time would make every interval wrong by the
    machine's offset, which is the kind of bug that only shows up after a
    deployment moves.
    """
    moment = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
