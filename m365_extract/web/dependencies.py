"""FastAPI dependency injection functions.

All dependencies pull from request.app.state, populated by the lifespan in app.py.
"""

from __future__ import annotations

from fastapi import Request

from m365_extract.auth.token_store import TokenStore
from m365_extract.config import Config
from m365_extract.user_manager import UserManager
from m365_extract.web.exceptions import WebConfigError


def get_config(request: Request) -> Config:
    """Retrieve the Config from app state."""
    config = getattr(request.app.state, "config", None)
    if config is None:
        msg = "Config not initialized in app state"
        raise WebConfigError(msg)
    return config


def get_token_store(request: Request) -> TokenStore:
    """Retrieve the TokenStore from app state."""
    store = getattr(request.app.state, "token_store", None)
    if store is None:
        msg = "TokenStore not initialized in app state"
        raise WebConfigError(msg)
    return store


def get_user_manager(request: Request) -> UserManager:
    """Retrieve the UserManager from app state."""
    manager = getattr(request.app.state, "user_manager", None)
    if manager is None:
        msg = "UserManager not initialized in app state"
        raise WebConfigError(msg)
    return manager
