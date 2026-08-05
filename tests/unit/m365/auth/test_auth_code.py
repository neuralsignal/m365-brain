"""Tests for auth code flow authentication."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from m365_brain.config import AuthConfig
from m365_brain.m365.auth.auth_code import AuthCodeAuth, AuthCodeError


@pytest.fixture()
def auth_config_with_secret(tmp_path):
    return AuthConfig(
        client_id="test-client-id",
        tenant_id="test-tenant-id",
        scopes=["User.Read", "Mail.Read", "offline_access", "openid", "profile"],
        token_cache_path=str(tmp_path / "token_cache.json"),
        client_secret="test-client-secret",
    )


@pytest.fixture()
def auth_config_without_secret(tmp_path):
    return AuthConfig(
        client_id="test-client-id",
        tenant_id="test-tenant-id",
        scopes=["User.Read", "Mail.Read"],
        token_cache_path=str(tmp_path / "token_cache.json"),
        client_secret=None,
    )


class TestAuthCodeAuth:
    def test_raises_without_client_secret(self, auth_config_without_secret):
        with pytest.raises(AuthCodeError, match="client_secret"):
            AuthCodeAuth(auth_config_without_secret)

    @patch("m365_brain.m365.auth.auth_code.msal.ConfidentialClientApplication")
    def test_creates_confidential_application(self, mock_app_cls, auth_config_with_secret):
        AuthCodeAuth(auth_config_with_secret)

        mock_app_cls.assert_called_once_with(
            "test-client-id",
            authority="https://login.microsoftonline.com/test-tenant-id",
            client_credential="test-client-secret",
        )

    @patch("m365_brain.m365.auth.auth_code.msal.ConfidentialClientApplication")
    def test_reserved_scopes_excluded(self, mock_app_cls, auth_config_with_secret):
        auth = AuthCodeAuth(auth_config_with_secret)

        assert "offline_access" not in auth._scopes
        assert "openid" not in auth._scopes
        assert "profile" not in auth._scopes
        assert "User.Read" in auth._scopes
        assert "Mail.Read" in auth._scopes

    @patch("m365_brain.m365.auth.auth_code.msal.ConfidentialClientApplication")
    def test_get_auth_url_returns_url(self, mock_app_cls, auth_config_with_secret):
        mock_app = MagicMock()
        mock_app_cls.return_value = mock_app
        mock_app.get_authorization_request_url.return_value = "https://login.microsoftonline.com/auth?code=abc"

        auth = AuthCodeAuth(auth_config_with_secret)
        url = auth.get_auth_url(redirect_uri="http://localhost:8000/auth/callback", state="random-state")

        assert url == "https://login.microsoftonline.com/auth?code=abc"
        mock_app.get_authorization_request_url.assert_called_once_with(
            auth._scopes,
            redirect_uri="http://localhost:8000/auth/callback",
            state="random-state",
        )

    @patch("m365_brain.m365.auth.auth_code.msal.ConfidentialClientApplication")
    def test_acquire_token_success(self, mock_app_cls, auth_config_with_secret):
        mock_app = MagicMock()
        mock_app_cls.return_value = mock_app
        mock_app.acquire_token_by_authorization_code.return_value = {
            "access_token": "test-access-token",
            "refresh_token": "test-refresh-token",
            "id_token_claims": {"name": "Test User"},
        }

        auth = AuthCodeAuth(auth_config_with_secret)
        result = auth.acquire_token_by_code(
            code="auth-code-123",
            redirect_uri="http://localhost:8000/auth/callback",
        )

        assert result["access_token"] == "test-access-token"
        mock_app.acquire_token_by_authorization_code.assert_called_once_with(
            "auth-code-123",
            scopes=auth._scopes,
            redirect_uri="http://localhost:8000/auth/callback",
        )

    @patch("m365_brain.m365.auth.auth_code.msal.ConfidentialClientApplication")
    def test_acquire_token_failure_raises(self, mock_app_cls, auth_config_with_secret):
        mock_app = MagicMock()
        mock_app_cls.return_value = mock_app
        mock_app.acquire_token_by_authorization_code.return_value = {
            "error": "invalid_grant",
            "error_description": "Code expired",
        }

        auth = AuthCodeAuth(auth_config_with_secret)

        with pytest.raises(AuthCodeError, match="Code expired"):
            auth.acquire_token_by_code(
                code="expired-code",
                redirect_uri="http://localhost:8000/auth/callback",
            )

    @patch("m365_brain.m365.auth.auth_code.msal.ConfidentialClientApplication")
    def test_refresh_token_success(self, mock_app_cls, auth_config_with_secret):
        mock_app = MagicMock()
        mock_app_cls.return_value = mock_app
        mock_app.acquire_token_by_refresh_token.return_value = {
            "access_token": "new-access-token",
            "refresh_token": "new-refresh-token",
        }

        auth = AuthCodeAuth(auth_config_with_secret)
        result = auth.refresh_token(refresh_token_value="old-refresh-token")

        assert result["access_token"] == "new-access-token"
        mock_app.acquire_token_by_refresh_token.assert_called_once_with(
            "old-refresh-token",
            scopes=auth._scopes,
        )

    @patch("m365_brain.m365.auth.auth_code.msal.ConfidentialClientApplication")
    def test_refresh_token_failure_raises(self, mock_app_cls, auth_config_with_secret):
        mock_app = MagicMock()
        mock_app_cls.return_value = mock_app
        mock_app.acquire_token_by_refresh_token.return_value = {
            "error": "invalid_grant",
            "error_description": "Refresh token expired",
        }

        auth = AuthCodeAuth(auth_config_with_secret)

        with pytest.raises(AuthCodeError, match="Refresh token expired"):
            auth.refresh_token(refresh_token_value="expired-refresh-token")
