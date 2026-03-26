"""Background daemon — runs sync loop in a thread inside the Reflex app."""

from __future__ import annotations

import threading
from pathlib import Path

import structlog

from m365_extract.daemon import run_daemon_cycle, write_health_file

log = structlog.get_logger()


def start_daemon(
    config,
    engine,
    token_adapter,
    state_dir: str,
    interval: int,
) -> threading.Event:
    """Start daemon sync loop in a background daemon thread.

    Returns a threading.Event that the caller can set() to stop the loop.
    """
    stop = threading.Event()

    def _loop() -> None:
        log.info("daemon_runner.started", interval=interval, state_dir=state_dir)
        Path(state_dir).mkdir(parents=True, exist_ok=True)
        while not stop.is_set():
            try:
                run_daemon_cycle(config, engine, token_adapter, state_dir)
                write_health_file(state_dir)
            except Exception:
                # Deliberate broad catch: inner sync_user() already handles domain
                # exceptions (GraphApiError, ExtractorError, etc.). This outer catch
                # is the last-resort safety net so the daemon thread survives truly
                # unexpected failures (e.g. SQLAlchemy session errors) rather than
                # silently dying. Constitution §4 deviation for daemon resilience.
                log.exception("daemon_runner.cycle_failed")
            stop.wait(timeout=interval)
        log.info("daemon_runner.stopped")

    thread = threading.Thread(target=_loop, daemon=True, name="sync-daemon")
    thread.start()
    return stop


def stop_daemon(stop_event: threading.Event) -> None:
    """Signal the daemon thread to stop."""
    stop_event.set()
