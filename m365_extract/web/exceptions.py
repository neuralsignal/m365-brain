"""Web-specific exception classes."""

from __future__ import annotations


class WebConfigError(Exception):
    """Raised when web configuration is missing or invalid."""


class SyncError(Exception):
    """Raised when a sync operation fails."""


class UserNotFoundError(Exception):
    """Raised when a requested user does not exist."""

    def __init__(self, user_id: str) -> None:
        self.user_id = user_id
        super().__init__(f"User '{user_id}' not found")


class AccessDeniedError(Exception):
    """Raised when a user tries to access another user's resources."""
