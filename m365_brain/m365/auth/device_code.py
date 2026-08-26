"""MSAL device code flow authentication for CLI mode.

Acquires, caches, and refreshes Graph API tokens using a public client application.

Every call that leaves the process is wrapped in ``auth_transport_errors`` --
including the constructor, because MSAL performs authority discovery there and
a first-use build of this class therefore happens *inside* ``GraphClient``'s
retry loop. Construction failing is safe to retry: no caller memoises the
instance until ``__init__`` returns.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import msal

from m365_brain.config import AuthConfig
from m365_brain.m365.auth.msal_http import TimeoutSession, auth_transport_errors

_RESERVED_SCOPES = {"offline_access", "openid", "profile"}


class DeviceCodeAuth:
    """Device code flow authenticator. Call get_token() to get a valid access token."""

    def __init__(self, auth_config: AuthConfig, timeout_seconds: int) -> None:
        self._config = auth_config
        self._cache = self._load_cache()
        with auth_transport_errors():
            self._app = msal.PublicClientApplication(
                auth_config.client_id,
                authority=f"https://login.microsoftonline.com/{auth_config.tenant_id}",
                token_cache=self._cache,
                http_client=TimeoutSession(timeout_seconds),
            )
        self._scopes = [s for s in auth_config.scopes if s not in _RESERVED_SCOPES]

    def get_token(self) -> str:
        """Acquire a valid Graph API access token. Tries cache first, then device code flow."""
        result = self._try_silent()
        if not result:
            result = self._device_code_flow()
        self._save_cache()
        return self._extract_token(result)

    def login(self) -> str:
        """Force interactive login via device code flow. Returns the access token."""
        result = self._device_code_flow()
        self._save_cache()
        return self._extract_token(result)

    def cached_token(self) -> str | None:
        """Return a token from the cache, or None. Never prompts.

        `get_token` falls back to the interactive flow, which makes it the
        wrong call for a status check -- asking "am I signed in?" would sign
        you in. This is the read-only half.
        """
        result = self._try_silent()
        if result is None:
            return None
        self._save_cache()
        return str(result["access_token"])

    def account_names(self) -> list[str]:
        """Usernames MSAL holds in this profile's cache."""
        with auth_transport_errors():
            accounts = self._app.get_accounts()
        return [str(account.get("username", "")) for account in accounts]

    def _try_silent(self) -> dict | None:
        """Try to acquire a token silently from the cache.

        ``get_accounts`` is inside the wrapper because it is not the pure cache
        read it looks like: when the cache holds nothing for this authority,
        MSAL falls through to instance discovery, which is an HTTP call over
        ``requests`` like any other.
        """
        with auth_transport_errors():
            accounts = self._app.get_accounts()
            if not accounts:
                return None
            result = self._app.acquire_token_silent(self._scopes, account=accounts[0])
        if result and "access_token" in result:
            return result
        return None

    def _device_code_flow(self) -> dict:
        """Acquire a token via device code flow. Blocks until the user authenticates."""
        with auth_transport_errors():
            flow = self._app.initiate_device_flow(scopes=self._scopes)
        if "user_code" not in flow:
            _fail(f"Failed to initiate device flow: {json.dumps(flow, indent=2)}")
        print(flow["message"])
        sys.stdout.flush()
        with auth_transport_errors():
            return self._app.acquire_token_by_device_flow(flow)

    def _load_cache(self) -> msal.SerializableTokenCache:
        cache = msal.SerializableTokenCache()
        cache_path = Path(self._config.token_cache_path)
        if cache_path.exists():
            cache.deserialize(cache_path.read_text(encoding="utf-8"))
        return cache

    def _save_cache(self) -> None:
        if self._cache.has_state_changed:
            cache_path = Path(self._config.token_cache_path)
            cache_path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(cache_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(self._cache.serialize())

    def _extract_token(self, result: dict) -> str:
        if "access_token" in result:
            return result["access_token"]
        error = result.get("error_description", result.get("error", "unknown error"))
        _fail(f"Token acquisition failed: {error}")
        return ""  # pragma: no cover


def _fail(message: str) -> None:
    print(f"Auth error: {message}", file=sys.stderr)
    raise SystemExit(1)
