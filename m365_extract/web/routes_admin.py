"""Admin endpoints for user management."""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends

from m365_extract.auth.token_store import TokenStore
from m365_extract.user_manager import UserManager
from m365_extract.web.dependencies import get_token_store, get_user_manager
from m365_extract.web.exceptions import UserNotFoundError

log = structlog.get_logger()

router = APIRouter(prefix="/admin")


@router.get("/users")
def list_users(user_manager: UserManager = Depends(get_user_manager)) -> dict:
    """List all managed users."""
    users = user_manager.list_users()
    return {
        "users": [
            {
                "user_id": u.user_id,
                "display_name": u.display_name,
                "email": u.email,
                "enabled": u.enabled,
                "created_at": u.created_at,
            }
            for u in users
        ]
    }


@router.post("/users/{user_id}/enable")
def enable_user(
    user_id: str,
    user_manager: UserManager = Depends(get_user_manager),
) -> dict:
    """Enable a user."""
    try:
        user_manager.set_enabled(user_id, enabled=True)
    except ValueError as exc:
        raise UserNotFoundError(user_id) from exc
    log.info("admin.user_enabled", user_id=user_id)
    return {"status": "enabled", "user_id": user_id}


@router.post("/users/{user_id}/disable")
def disable_user(
    user_id: str,
    user_manager: UserManager = Depends(get_user_manager),
) -> dict:
    """Disable a user."""
    try:
        user_manager.set_enabled(user_id, enabled=False)
    except ValueError as exc:
        raise UserNotFoundError(user_id) from exc
    log.info("admin.user_disabled", user_id=user_id)
    return {"status": "disabled", "user_id": user_id}


@router.delete("/users/{user_id}")
def delete_user(
    user_id: str,
    user_manager: UserManager = Depends(get_user_manager),
    token_store: TokenStore = Depends(get_token_store),
) -> dict:
    """Delete a user and their tokens."""
    token_store.delete_tokens(user_id)
    user_manager.delete_user(user_id)
    log.info("admin.user_deleted", user_id=user_id)
    return {"status": "deleted", "user_id": user_id}
