"""Sync trigger and status endpoints."""

from __future__ import annotations

import time

import structlog
from fastapi import APIRouter, Depends

from m365_extract.auth.token_provider import make_web_token_provider
from m365_extract.auth.token_store import TokenStore
from m365_extract.cli import _run_extractors
from m365_extract.config import Config
from m365_extract.state import SyncState
from m365_extract.storage import create_storage
from m365_extract.user_manager import UserManager
from m365_extract.web.dependencies import get_config, get_token_store, get_user_manager
from m365_extract.web.exceptions import SyncError, UserNotFoundError

log = structlog.get_logger()

router = APIRouter(prefix="/sync")

_last_sync: dict[str, float] = {}


@router.post("/{user_id}")
def trigger_sync(
    user_id: str,
    config: Config = Depends(get_config),
    token_store: TokenStore = Depends(get_token_store),
    user_manager: UserManager = Depends(get_user_manager),
) -> dict:
    """Trigger a one-shot sync for a user."""
    user = user_manager.get_user(user_id)
    if user is None:
        raise UserNotFoundError(user_id)

    token_provider = make_web_token_provider(
        token_store=token_store,
        user_id=user_id,
        auth_config=config.auth,
    )
    storage = create_storage(config.storage)
    sync_state = SyncState(config.state.state_file_path)
    names = list(config.extractors.__dataclass_fields__.keys())

    try:
        _run_extractors(config, token_provider, storage, sync_state, names)
    except Exception as exc:
        log.error("sync.failed", user_id=user_id, error=str(exc))
        raise SyncError(str(exc)) from exc

    _last_sync[user_id] = time.time()
    log.info("sync.completed", user_id=user_id)
    return {"status": "completed", "user_id": user_id}


@router.get("/{user_id}/status")
def sync_status(
    user_id: str,
    user_manager: UserManager = Depends(get_user_manager),
) -> dict:
    """Return the last sync time for a user."""
    user = user_manager.get_user(user_id)
    if user is None:
        raise UserNotFoundError(user_id)

    last = _last_sync.get(user_id)
    return {"user_id": user_id, "last_sync": last}
