"""Unified token provider interface.

Returns a callable that GraphClient can use to get tokens without knowing the auth flow.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Protocol, runtime_checkable

from m365_brain.auth.device_code import DeviceCodeAuth
from m365_brain.config import AuthConfig


@runtime_checkable
class TokenStoreProtocol(Protocol):
    """Protocol for token storage — decouples auth from the specific store implementation."""

    def get_tokens(self, user_id: str) -> dict | None: ...
    def store_tokens(self, user_id: str, tokens: dict) -> None: ...


_TOKEN_EXPIRY_BUFFER_SECONDS = 900


class TokenRefreshError(Exception):
    """Raised when a web token cannot be refreshed."""


def make_cli_token_provider(auth_config: AuthConfig) -> Callable[[], str]:
    """Create a token provider for CLI mode (device code flow)."""
    auth = DeviceCodeAuth(auth_config)
    return auth.get_token


def make_web_token_provider(
    token_store: TokenStoreProtocol,
    user_id: str,
    auth_config: AuthConfig,
) -> Callable[[], str]:
    """Create a token provider for web mode (auth code flow with auto-refresh).

    Thread-safe: concurrent calls are serialized via a lock.
    """
    from m365_brain.auth.auth_code import AuthCodeAuth

    auth = AuthCodeAuth(auth_config)
    lock = threading.Lock()

    def _get_token() -> str:
        with lock:
            tokens = token_store.get_tokens(user_id)
            if tokens is None:
                msg = f"No tokens stored for user '{user_id}'"
                raise TokenRefreshError(msg)

            expires_at = tokens.get("expires_at", 0)
            if time.time() < expires_at - _TOKEN_EXPIRY_BUFFER_SECONDS:
                return tokens["access_token"]

            refresh_token_value = tokens.get("refresh_token")
            if refresh_token_value is None:
                msg = f"No refresh token available for user '{user_id}'"
                raise TokenRefreshError(msg)

            refreshed = auth.refresh_token(refresh_token_value)
            updated_tokens = {
                **tokens,
                "access_token": refreshed["access_token"],
                "refresh_token": refreshed.get("refresh_token", refresh_token_value),
                "expires_at": time.time() + refreshed.get("expires_in", 3600),
            }
            token_store.store_tokens(user_id, updated_tokens)
            return refreshed["access_token"]

    return _get_token
