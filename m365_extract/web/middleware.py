"""Per-user and admin access control for web mode.

Ensures authenticated users can only access their own sync and status endpoints,
and that admin endpoints require a valid admin secret.
"""

from __future__ import annotations

from fastapi import Depends, Request

from m365_extract.config import Config
from m365_extract.web.dependencies import get_config
from m365_extract.web.exceptions import AccessDeniedError


def require_same_user(request: Request, user_id: str) -> None:
    """Verify that the session user matches the requested user_id.

    Raises AccessDeniedError if the session is missing or the user_id does not match.
    """
    session_user = request.session.get("user_id")
    if session_user is None:
        raise AccessDeniedError("Authentication required")
    if session_user != user_id:
        raise AccessDeniedError(f"User '{session_user}' cannot access resources for user '{user_id}'")


def require_admin(request: Request, config: Config = Depends(get_config)) -> None:  # noqa: B008
    """Verify that the request contains a valid admin secret header.

    Reads the expected secret from config.web.admin_secret and compares
    it against the X-Admin-Secret request header.

    Raises AccessDeniedError if the header is missing or does not match.
    """
    expected = config.web.admin_secret
    provided = request.headers.get("X-Admin-Secret")
    if provided is None:
        raise AccessDeniedError("Admin authentication required")
    if provided != expected:
        raise AccessDeniedError("Invalid admin secret")
