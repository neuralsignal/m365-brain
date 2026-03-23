"""Tests for device code authentication."""

from __future__ import annotations

import os
import stat
from unittest.mock import MagicMock, patch

import pytest

from m365_extract.auth.device_code import DeviceCodeAuth
from m365_extract.config import AuthConfig


@pytest.fixture()
def auth_config(tmp_path):
    return AuthConfig(
        client_id="test-client-id",
        tenant_id="test-tenant-id",
        scopes=["User.Read", "Mail.Read", "offline_access"],
        token_cache_path=str(tmp_path / "token_cache.json"),
        client_secret=None,
    )


class TestDeviceCodeAuth:
    @patch("m365_extract.auth.device_code.msal.PublicClientApplication")
    def test_get_token_from_cache(self, mock_app_cls, auth_config):
        mock_app = MagicMock()
        mock_app_cls.return_value = mock_app
        mock_app.get_accounts.return_value = [{"username": "user@example.com"}]
        mock_app.acquire_token_silent.return_value = {"access_token": "cached-token"}

        auth = DeviceCodeAuth(auth_config)
        token = auth.get_token()

        assert token == "cached-token"
        mock_app.acquire_token_silent.assert_called_once()

    @patch("m365_extract.auth.device_code.msal.PublicClientApplication")
    def test_get_token_device_flow_when_no_cache(self, mock_app_cls, auth_config):
        mock_app = MagicMock()
        mock_app_cls.return_value = mock_app
        mock_app.get_accounts.return_value = []
        mock_app.initiate_device_flow.return_value = {
            "user_code": "ABC123",
            "message": "Go to https://microsoft.com/devicelogin and enter ABC123",
        }
        mock_app.acquire_token_by_device_flow.return_value = {
            "access_token": "new-token",
        }

        auth = DeviceCodeAuth(auth_config)
        token = auth.get_token()

        assert token == "new-token"
        mock_app.initiate_device_flow.assert_called_once()

    @patch("m365_extract.auth.device_code.msal.PublicClientApplication")
    def test_reserved_scopes_excluded(self, mock_app_cls, auth_config):
        mock_app = MagicMock()
        mock_app_cls.return_value = mock_app
        mock_app.get_accounts.return_value = [{"username": "user@example.com"}]
        mock_app.acquire_token_silent.return_value = {"access_token": "token"}

        auth = DeviceCodeAuth(auth_config)
        auth.get_token()

        # offline_access should be filtered out from the scopes passed to MSAL
        called_scopes = mock_app.acquire_token_silent.call_args[0][0]
        assert "offline_access" not in called_scopes
        assert "User.Read" in called_scopes
        assert "Mail.Read" in called_scopes

    @patch("m365_extract.auth.device_code.msal.PublicClientApplication")
    def test_failed_device_flow_exits(self, mock_app_cls, auth_config):
        mock_app = MagicMock()
        mock_app_cls.return_value = mock_app
        mock_app.get_accounts.return_value = []
        mock_app.initiate_device_flow.return_value = {"error": "something went wrong"}

        auth = DeviceCodeAuth(auth_config)
        with pytest.raises(SystemExit):
            auth.get_token()

    @patch("m365_extract.auth.device_code.msal.PublicClientApplication")
    def test_token_acquisition_failure_exits(self, mock_app_cls, auth_config):
        mock_app = MagicMock()
        mock_app_cls.return_value = mock_app
        mock_app.get_accounts.return_value = []
        mock_app.initiate_device_flow.return_value = {
            "user_code": "ABC",
            "message": "Go to...",
        }
        mock_app.acquire_token_by_device_flow.return_value = {
            "error": "auth_failed",
            "error_description": "User cancelled",
        }

        auth = DeviceCodeAuth(auth_config)
        with pytest.raises(SystemExit):
            auth.get_token()

    @patch("m365_extract.auth.device_code.msal.PublicClientApplication")
    def test_cache_saved_on_state_change(self, mock_app_cls, auth_config, tmp_path):
        mock_app = MagicMock()
        mock_app_cls.return_value = mock_app
        mock_app.get_accounts.return_value = [{"username": "user@example.com"}]
        mock_app.acquire_token_silent.return_value = {"access_token": "token"}

        auth = DeviceCodeAuth(auth_config)
        # Simulate cache state change
        auth._cache.has_state_changed = True
        auth._cache.serialize = MagicMock(return_value='{"cached": true}')

        auth.get_token()

        cache_path = tmp_path / "token_cache.json"
        assert cache_path.exists()

    @patch("m365_extract.auth.device_code.msal.PublicClientApplication")
    def test_cache_file_has_restricted_permissions(self, mock_app_cls, auth_config, tmp_path):
        mock_app = MagicMock()
        mock_app_cls.return_value = mock_app
        mock_app.get_accounts.return_value = [{"username": "user@example.com"}]
        mock_app.acquire_token_silent.return_value = {"access_token": "token"}

        auth = DeviceCodeAuth(auth_config)
        auth._cache.has_state_changed = True
        auth._cache.serialize = MagicMock(return_value='{"cached": true}')

        auth.get_token()

        cache_path = tmp_path / "token_cache.json"
        file_mode = os.stat(cache_path).st_mode
        assert stat.S_IMODE(file_mode) == 0o600
