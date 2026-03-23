"""Per-user access control for web mode.

Ensures authenticated users can only access their own sync and status endpoints.
"""

from __future__ import annotations

from fastapi import Request

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
