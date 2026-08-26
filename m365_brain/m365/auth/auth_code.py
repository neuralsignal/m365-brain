"""MSAL authorization code flow for web mode.

Acquires tokens via OAuth2 auth code grant using a confidential client application.
Used by the FastAPI web service for multi-user authentication.
"""

from __future__ import annotations

import msal

from m365_brain.config import AuthConfig
from m365_brain.m365.auth.msal_http import TimeoutSession, auth_transport_errors

_RESERVED_SCOPES = {"offline_access", "openid", "profile"}


class AuthCodeError(Exception):
    """Raised when auth code flow encounters an error."""


class AuthCodeAuth:
    """Authorization code flow authenticator for web mode."""

    def __init__(self, auth_config: AuthConfig, timeout_seconds: int) -> None:
        if auth_config.client_secret is None:
            msg = "client_secret is required for auth code flow"
            raise AuthCodeError(msg)

        self._config = auth_config
        with auth_transport_errors():
            self._app = msal.ConfidentialClientApplication(
                auth_config.client_id,
                authority=f"https://login.microsoftonline.com/{auth_config.tenant_id}",
                client_credential=auth_config.client_secret.get_secret_value(),
                http_client=TimeoutSession(timeout_seconds),
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
        with auth_transport_errors():
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
        """Refresh an access token using a refresh token. Returns full MSAL response.

        The one method on this class that runs inside ``GraphClient``'s retry
        loop -- the web token provider calls it when a cached access token is
        within its expiry buffer. A transport fault here is what
        ``AuthTransportError`` exists for; an MSAL ``error`` dict is not, and
        still raises ``AuthCodeError`` on the first attempt.
        """
        with auth_transport_errors():
            result = self._app.acquire_token_by_refresh_token(
                refresh_token_value,
                scopes=self._scopes,
            )
        if "error" in result:
            error = result.get("error_description", result.get("error", "unknown error"))
            msg = f"Token refresh failed: {error}"
            raise AuthCodeError(msg)
        return result
