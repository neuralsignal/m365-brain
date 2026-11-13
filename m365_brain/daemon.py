"""The continuous loop around `cycle.run_once`.

**A failing unit backs off; only a needed login stops the daemon.** Every
failure lengthens that unit's own wait (`schedule.wait_seconds`) and logs one
`unit.backing_off` per failed cycle; the other units keep their schedule, and
the first success resets the streak, so a service that comes back needs no
restart. Only `cycle.AUTH_FAILURES` count toward
`service.max_consecutive_auth_failures`; past it the loop exits 1, because no
retry produces a token the cache cannot.

Observed 2026-11-13: Exchange answered 5xx for one mailbox for hours, the email
unit crossed the old any-failure ceiling, and the exit stopped OneDrive, Teams
and SharePoint with it. The persisted streak then exited every restart.
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from datetime import UTC, datetime

import structlog

from m365_brain.config import ConfigError
from m365_brain.cycle import Runtime, Selection, run_once, select_units
from m365_brain.schedule import Unit, last_run, next_attempt, next_wake, read_cursors

log = structlog.get_logger()


def run_forever(runtime: Runtime, selection: Selection, delay_seconds: int) -> int:
    """`run_once` on a schedule until a unit's auth-failure streak exceeds the cap.

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
        started = datetime.now(UTC)
        try:
            run_once(runtime, selection)
        except ConfigError:
            raise
        except Exception:  # noqa: BLE001 -- see the docstring
            log.exception("cycle.failed")

        units = select_units(runtime.config, selection.names)
        cursors = read_cursors(runtime.state, units)
        report_backoff(units, cursors, started)

        exhausted = exhausted_units(cursors, ceiling)
        if exhausted:
            log.critical("cycle.auth_failure_ceiling_reached", units=exhausted, ceiling=ceiling)
            return 1

        _sleep_until(next_wake(units, cursors, datetime.now(UTC)), slice_seconds)


def report_backoff(units: Sequence[Unit], cursors: dict[str, dict], since: datetime) -> None:
    """One `unit.backing_off` per unit whose run at or after `since` failed."""
    floor = since.replace(microsecond=0)
    for unit in units:
        cursor = cursors.get(unit.name, {})
        streak = int(cursor.get("consecutive_failures", 0))
        ran, attempt = last_run(cursor), next_attempt(unit, cursor)
        if ran is None or attempt is None or streak == 0 or ran < floor:
            continue  # never run, healthy, or its failure belongs to an earlier cycle
        log.error(
            "unit.backing_off",
            unit=unit.name,
            consecutive_failures=streak,
            consecutive_auth_failures=int(cursor.get("consecutive_auth_failures", 0)),
            next_attempt_at=attempt.isoformat(),
            last_error=cursor.get("last_error"),
        )


def exhausted_units(cursors: dict[str, dict], ceiling: int) -> list[str]:
    """Units whose consecutive auth failures exceed the ceiling, sorted."""
    return sorted(name for name, cursor in cursors.items() if int(cursor.get("consecutive_auth_failures", 0)) > ceiling)


def _sleep_until(wake: datetime, slice_seconds: int) -> None:
    """Sleep in bounded slices so a SIGTERM is answered promptly."""
    while True:
        remaining = (wake - datetime.now(UTC)).total_seconds()
        if remaining <= 0:
            return
        time.sleep(min(remaining, slice_seconds))
