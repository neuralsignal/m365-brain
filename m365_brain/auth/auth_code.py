"""MSAL authorization code flow for web mode.

Acquires tokens via OAuth2 auth code grant using a confidential client application.
Used by the FastAPI web service for multi-user authentication.
"""

from __future__ import annotations

import msal

from m365_brain.config import AuthConfig

_RESERVED_SCOPES = {"offline_access", "openid", "profile"}


class AuthCodeError(Exception):
    """Raised when auth code flow encounters an error."""


class AuthCodeAuth:
    """Authorization code flow authenticator for web mode."""

    def __init__(self, auth_config: AuthConfig) -> None:
        if auth_config.client_secret is None:
            msg = "client_secret is required for auth code flow"
            raise AuthCodeError(msg)

        self._config = auth_config
        self._app = msal.ConfidentialClientApplication(
            auth_config.client_id,
            authority=f"https://login.microsoftonline.com/{auth_config.tenant_id}",
            client_credential=auth_config.client_secret,
        )
        self._scopes = [s for s in auth_config.scopes if s not in _RESERVED_SCOPES]

    def get_auth_url(self, redirect_uri: str, state: str) -> str:
        """Generate the Entra authorization URL for the user to visit."""
        return self._app.get_authorization_request_url(
            self._scopes,
            redirect_uri=redirect_uri,
            state=state,
        )

    def acquire_token_by_code(self, code: str, redirect_uri: str) -> dict:
        """Exchange an authorization code for tokens. Returns full MSAL response."""
        result = self._app.acquire_token_by_authorization_code(
            code,
            scopes=self._scopes,
            redirect_uri=redirect_uri,
        )
        if "error" in result:
            error = result.get("error_description", result.get("error", "unknown error"))
            msg = f"Token acquisition failed: {error}"
            raise AuthCodeError(msg)
        return result

    def refresh_token(self, refresh_token_value: str) -> dict:
        """Refresh an access token using a refresh token. Returns full MSAL response."""
        result = self._app.acquire_token_by_refresh_token(
            refresh_token_value,
            scopes=self._scopes,
        )
        if "error" in result:
            error = result.get("error_description", result.get("error", "unknown error"))
            msg = f"Token refresh failed: {error}"
            raise AuthCodeError(msg)
        return result
