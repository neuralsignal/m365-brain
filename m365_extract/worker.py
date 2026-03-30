"""Sync worker — runs (user, extractor) jobs independently via a thread pool.

Separated from the Reflex admin UI. Communicates only through PostgreSQL:
reads users + preferences, writes ExtractorStatus rows.
Can run as a standalone process (`m365-extract worker`) or as a thread
within the Reflex app via `start_worker_thread()`.
"""

from __future__ import annotations

import hashlib
import struct
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path

import structlog
from sqlalchemy import text
from sqlmodel import Session, select

from m365_extract.auth.token_provider import TokenRefreshError, TokenStoreProtocol, make_web_token_provider
from m365_extract.config import Config
from m365_extract.config.errors import ConfigError
from m365_extract.extractors.errors import ExtractorError
from m365_extract.graph_client import GraphApiError
from m365_extract.models import ExtractorPreference, ExtractorStatus, User
from m365_extract.state import SyncState
from m365_extract.storage import create_user_storage
from m365_extract.sync import EXTRACTORS, run_extractors

log = structlog.get_logger()


def _lock_key(user_id: str, extractor_name: str) -> int:
    """Derive a stable int64 key for PostgreSQL advisory locks."""
    digest = hashlib.sha256(f"{user_id}:{extractor_name}".encode()).digest()
    return struct.unpack(">q", digest[:8])[0]


def try_advisory_lock(engine, user_id: str, extractor_name: str) -> bool:
    """Try to acquire a PostgreSQL advisory lock. Returns True if acquired."""
    key = _lock_key(user_id, extractor_name)
    with Session(engine) as session:
        result = session.exec(text("SELECT pg_try_advisory_lock(:key)").bindparams(key=key))
        return result.one()[0]


def release_advisory_lock(engine, user_id: str, extractor_name: str) -> None:
    """Release a PostgreSQL advisory lock."""
    key = _lock_key(user_id, extractor_name)
    with Session(engine) as session:
        session.exec(text("SELECT pg_advisory_unlock(:key)").bindparams(key=key))


def upsert_extractor_status(
    engine,
    user_id: str,
    extractor_name: str,
    status: str,
    items_synced: int,
    error_message: str | None,
) -> None:
    """Insert or update the ExtractorStatus row for a (user, extractor) pair."""
    with Session(engine) as session:
        statement = select(ExtractorStatus).where(
            ExtractorStatus.user_id == user_id,
            ExtractorStatus.extractor_name == extractor_name,
        )
        existing = session.exec(statement).first()
        now = datetime.now(tz=UTC)

        if existing:
            existing.status = status
            existing.last_run_at = now
            existing.items_synced = items_synced
            existing.error_message = error_message
            session.add(existing)
        else:
            row = ExtractorStatus(
                user_id=user_id,
                extractor_name=extractor_name,
                status=status,
                last_run_at=now,
                items_synced=items_synced,
                error_message=error_message,
            )
            session.add(row)
        session.commit()


def get_enabled_users(engine) -> list[User]:
    """Return all users with enabled=True."""
    with Session(engine) as session:
        statement = select(User).where(User.enabled == True).order_by(User.user_id)  # noqa: E712
        return list(session.exec(statement).all())


def get_user_extractors(engine, user_id: str) -> list[str]:
    """Return extractor names explicitly enabled for a user. No fallback — no preferences = nothing runs."""
    with Session(engine) as session:
        statement = select(ExtractorPreference.extractor_name).where(
            ExtractorPreference.user_id == user_id,
            ExtractorPreference.enabled == True,  # noqa: E712
        )
        return list(session.exec(statement).all())


def get_due_jobs(engine, config: Config) -> list[tuple[User, str]]:
    """Return (user, extractor_name) pairs that are due for a sync run."""
    users = get_enabled_users(engine)
    due: list[tuple[User, str]] = []
    now = datetime.now(tz=UTC)

    for user in users:
        extractor_names = get_user_extractors(engine, user.user_id)
        for ext_name in extractor_names:
            if ext_name not in EXTRACTORS:
                continue
            _, config_getter, _ = EXTRACTORS[ext_name]
            ext_config = config_getter(config)
            interval_seconds = ext_config.poll_interval_minutes * 60

            with Session(engine) as session:
                statement = select(ExtractorStatus).where(
                    ExtractorStatus.user_id == user.user_id,
                    ExtractorStatus.extractor_name == ext_name,
                )
                status_row = session.exec(statement).first()

            if status_row is None or status_row.last_run_at is None:
                due.append((user, ext_name))
            else:
                # SQLite strips timezone info; ensure both sides match
                last_run = status_row.last_run_at
                if last_run.tzinfo is None:
                    last_run = last_run.replace(tzinfo=UTC)
                if (now - last_run).total_seconds() >= interval_seconds:
                    due.append((user, ext_name))

    return due


def run_single_extractor(
    config: Config,
    engine,
    token_adapter: TokenStoreProtocol,
    user: User,
    extractor_name: str,
    state_dir: str,
) -> None:
    """Run one extractor for one user. Unit of work for the thread pool."""
    log.info("worker.job_started", user_id=user.user_id, extractor=extractor_name)
    upsert_extractor_status(engine, user.user_id, extractor_name, "running", 0, None)

    try:
        token_provider = make_web_token_provider(token_adapter, user.user_id, config.auth)
        storage = create_user_storage(config.storage, user.user_id)

        state_path = str(Path(state_dir) / user.user_id / f"{extractor_name}.json")
        sync_state = SyncState(state_path)

        total_items = run_extractors(config, token_provider, storage, sync_state, [extractor_name])

        upsert_extractor_status(engine, user.user_id, extractor_name, "success", total_items, None)
        log.info("worker.job_completed", user_id=user.user_id, extractor=extractor_name, items=total_items)
    except (GraphApiError, ExtractorError, ConfigError, TokenRefreshError) as exc:
        upsert_extractor_status(engine, user.user_id, extractor_name, "failed", 0, str(exc))
        log.error("worker.job_failed", user_id=user.user_id, extractor=extractor_name, error=str(exc))
    finally:
        release_advisory_lock(engine, user.user_id, extractor_name)


def _run_cycle(
    config: Config,
    engine,
    token_adapter: TokenStoreProtocol,
    state_dir: str,
    max_workers: int,
) -> None:
    """Execute one polling cycle: get due jobs, acquire locks, submit to pool, collect results."""
    due = get_due_jobs(engine, config)
    if not due:
        return

    log.info("worker.jobs_due", count=len(due))
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {}
        for user, ext_name in due:
            if try_advisory_lock(engine, user.user_id, ext_name):
                future = pool.submit(
                    run_single_extractor,
                    config,
                    engine,
                    token_adapter,
                    user,
                    ext_name,
                    state_dir,
                )
                futures[future] = (user.user_id, ext_name)
            else:
                log.debug("worker.job_locked", user_id=user.user_id, extractor=ext_name)
        for future in as_completed(futures):
            uid, ext = futures[future]
            try:
                future.result()
            except Exception:
                log.exception("worker.job_unexpected_error", user_id=uid, extractor=ext)


def worker_loop(config: Config, engine, token_adapter: TokenStoreProtocol, state_dir: str) -> None:
    """Main worker loop. Polls for due jobs, submits to thread pool."""
    worker_config = config.worker
    max_workers = worker_config.max_concurrent_jobs if worker_config else 4
    poll_interval = worker_config.poll_interval_seconds if worker_config else config.service.continuous_poll_seconds

    log.info("worker.started", max_workers=max_workers, poll_interval=poll_interval, state_dir=state_dir)
    Path(state_dir).mkdir(parents=True, exist_ok=True)

    try:
        while True:
            try:
                _run_cycle(config, engine, token_adapter, state_dir, max_workers)
            except Exception:
                log.exception("worker.cycle_failed")

            time.sleep(poll_interval)
    except KeyboardInterrupt:
        log.info("worker.stopped")


def start_worker_thread(
    config: Config,
    engine,
    token_adapter: TokenStoreProtocol,
    state_dir: str,
) -> threading.Event:
    """Start worker loop in a background daemon thread.

    Returns a threading.Event that the caller can set() to stop the loop.
    Used by the Reflex app for single-container deployment.
    """
    worker_config = config.worker
    poll_interval = worker_config.poll_interval_seconds if worker_config else config.service.continuous_poll_seconds
    max_workers = worker_config.max_concurrent_jobs if worker_config else 4

    stop = threading.Event()

    def _loop() -> None:
        log.info("worker.thread_started", max_workers=max_workers, poll_interval=poll_interval)
        Path(state_dir).mkdir(parents=True, exist_ok=True)

        while not stop.is_set():
            try:
                _run_cycle(config, engine, token_adapter, state_dir, max_workers)
            except Exception:
                log.exception("worker.cycle_failed")
            stop.wait(timeout=poll_interval)

        log.info("worker.thread_stopped")

    thread = threading.Thread(target=_loop, daemon=True, name="sync-worker")
    thread.start()
    return stop
