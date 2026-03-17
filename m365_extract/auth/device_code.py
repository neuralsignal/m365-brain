"""MSAL device code flow authentication for CLI mode.

Acquires, caches, and refreshes Graph API tokens using a public client application.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import msal

from m365_extract.config import AuthConfig

_RESERVED_SCOPES = {"offline_access", "openid", "profile"}


class DeviceCodeAuth:
    """Device code flow authenticator. Call get_token() to get a valid access token."""

    def __init__(self, auth_config: AuthConfig) -> None:
        self._config = auth_config
        self._cache = self._load_cache()
        self._app = msal.PublicClientApplication(
            auth_config.client_id,
            authority=f"https://login.microsoftonline.com/{auth_config.tenant_id}",
            token_cache=self._cache,
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

    def _try_silent(self) -> dict | None:
        """Try to acquire a token silently from the cache."""
        accounts = self._app.get_accounts()
        if not accounts:
            return None
        result = self._app.acquire_token_silent(self._scopes, account=accounts[0])
        if result and "access_token" in result:
            return result
        return None

    def _device_code_flow(self) -> dict:
        """Acquire a token via device code flow. Blocks until the user authenticates."""
        flow = self._app.initiate_device_flow(scopes=self._scopes)
        if "user_code" not in flow:
            _fail(f"Failed to initiate device flow: {json.dumps(flow, indent=2)}")
        print(flow["message"])
        sys.stdout.flush()
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
            cache_path.write_text(self._cache.serialize(), encoding="utf-8")

    def _extract_token(self, result: dict) -> str:
        if "access_token" in result:
            return result["access_token"]
        error = result.get("error_description", result.get("error", "unknown error"))
        _fail(f"Token acquisition failed: {error}")
        return ""  # unreachable, satisfies type checker


def _fail(message: str) -> None:
    print(f"Auth error: {message}", file=sys.stderr)
    raise SystemExit(1)
