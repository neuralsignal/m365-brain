"""Tests for m365_brain.m365.auth.token_provider."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

from m365_brain.config import AuthConfig
from m365_brain.m365.auth.token_provider import TokenRefreshError, make_cli_token_provider, make_web_token_provider

# `graph.timeout_seconds` in production; MSAL is mocked throughout.
TIMEOUT_SECONDS = 30


def _auth_config() -> AuthConfig:
    return AuthConfig(
        client_id="test-client-id",
        tenant_id="test-tenant-id",
        scopes=["User.Read"],
        token_cache_path="/tmp/test_cache.json",
        client_secret=None,
    )


def _web_auth_config() -> AuthConfig:
    return AuthConfig(
        client_id="test-client-id",
        tenant_id="test-tenant-id",
        scopes=["User.Read"],
        token_cache_path="/tmp/test_cache.json",
        client_secret="test-secret",
    )


@patch("m365_brain.m365.auth.token_provider.DeviceCodeAuth")
def test_returns_callable(mock_device_code_auth: MagicMock) -> None:
    result = make_cli_token_provider(_auth_config(), TIMEOUT_SECONDS)
    assert callable(result)


@patch("m365_brain.m365.auth.token_provider.DeviceCodeAuth")
def test_returns_get_token_bound_method(mock_device_code_auth: MagicMock) -> None:
    mock_instance = MagicMock()
    mock_device_code_auth.return_value = mock_instance

    result = make_cli_token_provider(_auth_config(), TIMEOUT_SECONDS)

    assert result is mock_instance.get_token


@patch("m365_brain.m365.auth.token_provider.DeviceCodeAuth")
def test_passes_auth_config_to_device_code_auth(mock_device_code_auth: MagicMock) -> None:
    config = _auth_config()
    make_cli_token_provider(config, TIMEOUT_SECONDS)

    mock_device_code_auth.assert_called_once_with(config, TIMEOUT_SECONDS)


@patch("m365_brain.m365.auth.auth_code.msal.ConfidentialClientApplication")
class TestWebTokenProvider:
    def test_returns_cached_token_when_valid(self, _mock_msal):
        mock_store = MagicMock()
        mock_store.get_tokens.return_value = {
            "access_token": "valid-token",
            "expires_at": time.time() + 1800,
            "refresh_token": "rt-123",
        }

        provider = make_web_token_provider(
            token_store=mock_store,
            user_id="user-1",
            auth_config=_web_auth_config(),
            timeout_seconds=TIMEOUT_SECONDS,
        )
        token = provider()

        assert token == "valid-token"

    def test_refreshes_expired_token(self, mock_msal_cls):
        mock_store = MagicMock()
        mock_store.get_tokens.return_value = {
            "access_token": "expired-token",
            "expires_at": time.time() - 100,
            "refresh_token": "rt-old",
        }

        mock_app = MagicMock()
        mock_msal_cls.return_value = mock_app
        mock_app.acquire_token_by_refresh_token.return_value = {
            "access_token": "new-token",
            "refresh_token": "rt-new",
            "expires_in": 3600,
        }

        provider = make_web_token_provider(
            token_store=mock_store,
            user_id="user-1",
            auth_config=_web_auth_config(),
            timeout_seconds=TIMEOUT_SECONDS,
        )
        token = provider()

        assert token == "new-token"
        mock_app.acquire_token_by_refresh_token.assert_called_once()
        mock_store.store_tokens.assert_called_once()

    def test_raises_when_no_tokens(self, _mock_msal):
        mock_store = MagicMock()
        mock_store.get_tokens.return_value = None

        provider = make_web_token_provider(
            token_store=mock_store,
            user_id="user-1",
            auth_config=_web_auth_config(),
            timeout_seconds=TIMEOUT_SECONDS,
        )

        with pytest.raises(TokenRefreshError, match="No tokens stored"):
            provider()

    def test_raises_when_no_refresh_token(self, _mock_msal):
        mock_store = MagicMock()
        mock_store.get_tokens.return_value = {
            "access_token": "expired-token",
            "expires_at": time.time() - 100,
        }

        provider = make_web_token_provider(
            token_store=mock_store,
            user_id="user-1",
            auth_config=_web_auth_config(),
            timeout_seconds=TIMEOUT_SECONDS,
        )

        with pytest.raises(TokenRefreshError, match="No refresh token"):
            provider()
