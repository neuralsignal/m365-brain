"""Health check endpoint."""

from __future__ import annotations

import importlib.metadata

from fastapi import APIRouter, Depends

from m365_extract.user_manager import UserManager
from m365_extract.web.dependencies import get_user_manager

router = APIRouter()


@router.get("/health")
def health(user_manager: UserManager = Depends(get_user_manager)) -> dict:
    """Return service health status."""
    version = importlib.metadata.version("m365-extract")
    users = user_manager.list_users()
    return {"status": "ok", "version": version, "users": len(users)}
