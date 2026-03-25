"""Daemon sync runner — syncs enabled users on a schedule.

Reads User + ExtractorPreference from the database, runs extractors via sync.py,
and writes SyncRecord rows so the admin UI can display sync history.

Lives in m365_extract/ (core library) because it uses sync.py, state.py, storage/,
and graph_client.py directly. The m365_admin/ package is only needed for the adapter.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import structlog
from sqlmodel import Session, select

from m365_extract.auth.token_provider import TokenStoreProtocol, make_web_token_provider
from m365_extract.config import Config
from m365_extract.models import ExtractorPreference, SyncRecord, User
from m365_extract.state import SyncState
from m365_extract.storage import create_storage
from m365_extract.sync import EXTRACTORS, run_extractors

log = structlog.get_logger()


def get_enabled_users(engine) -> list[User]:
    """Return all users with enabled=True."""
    with Session(engine) as session:
        statement = select(User).where(User.enabled == True).order_by(User.user_id)  # noqa: E712
        return list(session.exec(statement).all())


def get_user_extractors(engine, user_id: str) -> list[str]:
    """Return extractor names enabled for a specific user."""
    with Session(engine) as session:
        statement = (
            select(ExtractorPreference.extractor_name).where(
                ExtractorPreference.user_id == user_id, ExtractorPreference.enabled == True
            )  # noqa: E712
        )
        return list(session.exec(statement).all())


def write_sync_record(engine, record: SyncRecord) -> None:
    """Persist a SyncRecord (insert or update)."""
    with Session(engine) as session:
        session.add(record)
        session.commit()
        session.refresh(record)


def sync_user(
    config: Config,
    engine,
    token_adapter: TokenStoreProtocol,
    user: User,
    state_dir: str,
) -> SyncRecord:
    """Sync one user: create SyncRecord(running), run extractors, update to completed/failed."""
    extractor_names = get_user_extractors(engine, user.user_id)
    if not extractor_names:
        # No DB preferences: fall back to config-enabled extractors
        extractor_names = [name for name, (_, cfg_getter, _) in EXTRACTORS.items() if cfg_getter(config).enabled]

    now = datetime.now(tz=UTC)
    record = SyncRecord(
        user_id=user.user_id,
        started_at=now,
        status="running",
        extractors_run=json.dumps(extractor_names),
    )
    write_sync_record(engine, record)

    try:
        token_provider = make_web_token_provider(token_adapter, user.user_id, config.auth)
        storage = create_storage(config.storage)

        # Per-user sync state: state_dir/{user_id}/sync_state.json
        user_state_path = str(Path(state_dir) / user.user_id / "sync_state.json")
        sync_state = SyncState(user_state_path)

        total_items = run_extractors(config, token_provider, storage, sync_state, extractor_names)

        record.status = "completed"
        record.completed_at = datetime.now(tz=UTC)
        record.items_synced = total_items
        log.info("daemon.sync_user_completed", user_id=user.user_id)
    except Exception as exc:
        record.status = "failed"
        record.completed_at = datetime.now(tz=UTC)
        record.error_message = str(exc)
        log.error("daemon.sync_user_failed", user_id=user.user_id, error=str(exc))

    write_sync_record(engine, record)
    return record


def run_daemon_cycle(config: Config, engine, token_adapter: TokenStoreProtocol, state_dir: str) -> list[SyncRecord]:
    """Run one daemon cycle: sync all enabled users."""
    users = get_enabled_users(engine)
    if not users:
        log.info("daemon.no_enabled_users")
        return []

    records = []
    for user in users:
        log.info("daemon.syncing_user", user_id=user.user_id, email=user.email)
        record = sync_user(config, engine, token_adapter, user, state_dir)
        records.append(record)

    log.info("daemon.cycle_complete", users_synced=len(records))
    return records


def write_health_file(state_dir: str) -> None:
    """Write a health file with the current timestamp for Docker HEALTHCHECK."""
    health_path = Path(state_dir) / "daemon_health.json"
    health_path.parent.mkdir(parents=True, exist_ok=True)
    health_path.write_text(json.dumps({"last_cycle_completed": datetime.now(tz=UTC).isoformat()}))
