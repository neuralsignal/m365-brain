"""Sync trigger and status endpoints."""

from __future__ import annotations

import time
from dataclasses import replace

import structlog
from fastapi import APIRouter, Depends, Request

from m365_extract.auth.token_provider import make_web_token_provider
from m365_extract.auth.token_store import TokenStore
from m365_extract.config import Config
from m365_extract.state import SyncState
from m365_extract.storage import create_storage
from m365_extract.sync import run_extractors
from m365_extract.user_manager import UserManager
from m365_extract.web.dependencies import get_config, get_token_store, get_user_manager
from m365_extract.web.exceptions import SyncError, UserNotFoundError
from m365_extract.web.middleware import require_same_user

log = structlog.get_logger()

router = APIRouter(prefix="/sync")

_last_sync: dict[str, float] = {}


def _user_scoped_storage(config: Config, user_id: str):
    """Create a storage backend with a user-scoped base path for data isolation."""
    storage_config = config.storage
    if storage_config.local is not None:
        user_base = f"{storage_config.local.base_path}/{user_id}"
        user_local = replace(storage_config.local, base_path=user_base)
        storage_config = replace(storage_config, local=user_local)
    return create_storage(storage_config)


@router.post("/{user_id}")
def trigger_sync(
    user_id: str,
    request: Request,
    config: Config = Depends(get_config),
    token_store: TokenStore = Depends(get_token_store),
    user_manager: UserManager = Depends(get_user_manager),
) -> dict:
    """Trigger a one-shot sync for a user."""
    require_same_user(request, user_id)

    user = user_manager.get_user(user_id)
    if user is None:
        raise UserNotFoundError(user_id)

    token_provider = make_web_token_provider(
        token_store=token_store,
        user_id=user_id,
        auth_config=config.auth,
    )
    storage = _user_scoped_storage(config, user_id)
    sync_state = SyncState(config.state.state_file_path)
    names = list(config.extractors.__dataclass_fields__.keys())

    try:
        run_extractors(config, token_provider, storage, sync_state, names)
    except Exception as exc:
        log.error("sync.failed", user_id=user_id, error=str(exc))
        raise SyncError(str(exc)) from exc

    _last_sync[user_id] = time.time()
    log.info("sync.completed", user_id=user_id)
    return {"status": "completed", "user_id": user_id}


@router.get("/{user_id}/status")
def sync_status(
    user_id: str,
    request: Request,
    user_manager: UserManager = Depends(get_user_manager),
) -> dict:
    """Return the last sync time for a user."""
    require_same_user(request, user_id)

    user = user_manager.get_user(user_id)
    if user is None:
        raise UserNotFoundError(user_id)

    last = _last_sync.get(user_id)
    return {"user_id": user_id, "last_sync": last}
