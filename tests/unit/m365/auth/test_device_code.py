"""Tests for device code authentication."""

from __future__ import annotations

import os
import stat
from unittest.mock import MagicMock, patch

import msal
import pytest

from m365_brain.config import AuthConfig
from m365_brain.m365.auth.device_code import DeviceCodeAuth
from m365_brain.m365.errors import AuthRequiredError, TokenCacheError

# `graph.timeout_seconds` in production; MSAL is mocked in every test here.
TIMEOUT_SECONDS = 30


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
    @patch("m365_brain.m365.auth.device_code.msal.PublicClientApplication")
    def test_get_token_from_cache(self, mock_app_cls, auth_config):
        mock_app = MagicMock()
        mock_app_cls.return_value = mock_app
        mock_app.get_accounts.return_value = [{"username": "user@example.com"}]
        mock_app.acquire_token_silent.return_value = {"access_token": "cached-token"}

        auth = DeviceCodeAuth(auth_config, TIMEOUT_SECONDS)
        token = auth.get_token()

        assert token == "cached-token"
        mock_app.acquire_token_silent.assert_called_once()

    @patch("m365_brain.m365.auth.device_code.msal.PublicClientApplication")
    def test_get_token_raises_rather_than_prompting_when_no_cache(self, mock_app_cls, auth_config, tmp_path):
        """The whole fix. `get_token` is the daemon's provider too, and a
        device-code prompt in a process with no terminal blocks until the code
        expires and then keeps blocking."""
        mock_app = MagicMock()
        mock_app_cls.return_value = mock_app
        mock_app.get_accounts.return_value = []

        auth = DeviceCodeAuth(auth_config, TIMEOUT_SECONDS)
        with pytest.raises(AuthRequiredError) as excinfo:
            auth.get_token()

        mock_app.initiate_device_flow.assert_not_called()
        mock_app.acquire_token_by_device_flow.assert_not_called()
        assert str(tmp_path / "token_cache.json") in str(excinfo.value)
        assert "auth login" in str(excinfo.value)

    @patch("m365_brain.m365.auth.device_code.msal.PublicClientApplication")
    def test_get_token_raises_when_the_refresh_token_is_dead(self, mock_app_cls, auth_config):
        """An account exists but MSAL cannot silently renew it -- the other
        route into the old prompt, and the one a laptop actually hits."""
        mock_app = MagicMock()
        mock_app_cls.return_value = mock_app
        mock_app.get_accounts.return_value = [{"username": "user@example.com"}]
        mock_app.acquire_token_silent.return_value = {"error": "interaction_required"}

        auth = DeviceCodeAuth(auth_config, TIMEOUT_SECONDS)
        with pytest.raises(AuthRequiredError):
            auth.get_token()

        mock_app.initiate_device_flow.assert_not_called()

    def test_the_error_is_not_a_graph_error_and_not_transient(self):
        """Per-item extractor handlers catch GraphApiError; swallowing "no
        credentials" per item would skip every item and report success. And a
        retry cannot mint a refresh token, so the outbox must not put the
        intent back the way it does for AuthTransportError."""
        from m365_brain.m365.errors import GraphApiError

        assert not issubclass(AuthRequiredError, GraphApiError)
        assert getattr(AuthRequiredError("boom"), "transient", False) is False

    @patch("m365_brain.m365.auth.device_code.msal.PublicClientApplication")
    def test_reserved_scopes_excluded(self, mock_app_cls, auth_config):
        mock_app = MagicMock()
        mock_app_cls.return_value = mock_app
        mock_app.get_accounts.return_value = [{"username": "user@example.com"}]
        mock_app.acquire_token_silent.return_value = {"access_token": "token"}

        auth = DeviceCodeAuth(auth_config, TIMEOUT_SECONDS)
        auth.get_token()

        # offline_access should be filtered out from the scopes passed to MSAL
        called_scopes = mock_app.acquire_token_silent.call_args[0][0]
        assert "offline_access" not in called_scopes
        assert "User.Read" in called_scopes
        assert "Mail.Read" in called_scopes

    @patch("m365_brain.m365.auth.device_code.msal.PublicClientApplication")
    def test_failed_device_flow_exits(self, mock_app_cls, auth_config):
        mock_app = MagicMock()
        mock_app_cls.return_value = mock_app
        mock_app.get_accounts.return_value = []
        mock_app.initiate_device_flow.return_value = {"error": "something went wrong"}

        auth = DeviceCodeAuth(auth_config, TIMEOUT_SECONDS)
        with pytest.raises(SystemExit):
            auth.login()

    @patch("m365_brain.m365.auth.device_code.msal.PublicClientApplication")
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

        auth = DeviceCodeAuth(auth_config, TIMEOUT_SECONDS)
        with pytest.raises(SystemExit):
            auth.login()

    @patch("m365_brain.m365.auth.device_code.msal.PublicClientApplication")
    def test_cache_saved_on_state_change(self, mock_app_cls, auth_config, tmp_path):
        mock_app = MagicMock()
        mock_app_cls.return_value = mock_app
        mock_app.get_accounts.return_value = [{"username": "user@example.com"}]
        mock_app.acquire_token_silent.return_value = {"access_token": "token"}

        auth = DeviceCodeAuth(auth_config, TIMEOUT_SECONDS)
        # Simulate cache state change
        auth._cache.has_state_changed = True
        auth._cache.serialize = MagicMock(return_value='{"cached": true}')

        auth.get_token()

        cache_path = tmp_path / "token_cache.json"
        assert cache_path.exists()

    @patch("m365_brain.m365.auth.device_code.msal.PublicClientApplication")
    def test_cache_file_has_restricted_permissions(self, mock_app_cls, auth_config, tmp_path):
        mock_app = MagicMock()
        mock_app_cls.return_value = mock_app
        mock_app.get_accounts.return_value = [{"username": "user@example.com"}]
        mock_app.acquire_token_silent.return_value = {"access_token": "token"}

        auth = DeviceCodeAuth(auth_config, TIMEOUT_SECONDS)
        auth._cache.has_state_changed = True
        auth._cache.serialize = MagicMock(return_value='{"cached": true}')

        auth.get_token()

        cache_path = tmp_path / "token_cache.json"
        file_mode = os.stat(cache_path).st_mode
        assert stat.S_IMODE(file_mode) == 0o600

    @patch("m365_brain.m365.auth.device_code.msal.PublicClientApplication")
    def test_login_forces_device_code_flow(self, mock_app_cls, auth_config):
        mock_app = MagicMock()
        mock_app_cls.return_value = mock_app
        mock_app.initiate_device_flow.return_value = {
            "user_code": "XYZ789",
            "message": "Go to https://microsoft.com/devicelogin",
        }
        mock_app.acquire_token_by_device_flow.return_value = {
            "access_token": "login-token",
        }

        auth = DeviceCodeAuth(auth_config, TIMEOUT_SECONDS)
        token = auth.login()

        assert token == "login-token"
        mock_app.initiate_device_flow.assert_called_once()
        mock_app.acquire_token_by_device_flow.assert_called_once()
        mock_app.get_accounts.assert_not_called()
        mock_app.acquire_token_silent.assert_not_called()

    @patch("m365_brain.m365.auth.device_code.msal.PublicClientApplication")
    def test_try_silent_returns_none_without_access_token(self, mock_app_cls, auth_config):
        mock_app = MagicMock()
        mock_app_cls.return_value = mock_app
        mock_app.get_accounts.return_value = [{"username": "user@example.com"}]
        mock_app.acquire_token_silent.return_value = {
            "error": "interaction_required",
        }

        auth = DeviceCodeAuth(auth_config, TIMEOUT_SECONDS)
        result = auth._try_silent()

        assert result is None

    def test_load_cache_deserialises_existing_file(self, auth_config, tmp_path):
        cache = msal.SerializableTokenCache()
        cache_path = tmp_path / "token_cache.json"
        cache_path.write_text(cache.serialize(), encoding="utf-8")

        with patch("m365_brain.m365.auth.device_code.msal.PublicClientApplication"):
            auth = DeviceCodeAuth(auth_config, TIMEOUT_SECONDS)

        assert not auth._cache.has_state_changed


class TestCacheIoFailure:
    """Token-cache file I/O must not surface as a bare `OSError`.

    `_load_cache` read and `_save_cache` wrote `token_cache_path` with plain
    `pathlib`/`os` calls, so a full disk, a read-only mount or a bad path
    raised `OSError`. Both run inside the token provider, which
    `GraphClient._headers` calls from *within* the retry envelope -- and that
    envelope catches transport errors, not filesystem ones. The `OSError`
    therefore sailed through the transport and landed in
    `_attachment_helpers.py`'s `except (..., OSError)` arm, which read it as an
    unreadable attachment: logged at warning, attachment skipped, next one
    tried, and the extractor then recorded a **successful** sync with every
    attachment missing. Same reachability at `_file_helpers.py:191` and
    `commands/_catalog.py:226`.

    A directory where the cache file belongs is one cheap spelling of "this
    path cannot be read or written"; a full disk takes the same branch.
    """

    @pytest.fixture()
    def mock_app_cls(self):
        with patch("m365_brain.m365.auth.device_code.msal.PublicClientApplication") as mock:
            yield mock

    @pytest.fixture()
    def unwritable(self, mock_app_cls, auth_config, tmp_path):
        mock_app = MagicMock()
        mock_app_cls.return_value = mock_app
        mock_app.get_accounts.return_value = [{"username": "user@example.com"}]
        mock_app.acquire_token_silent.return_value = {"access_token": "token"}

        auth = DeviceCodeAuth(auth_config, TIMEOUT_SECONDS)
        auth._cache.has_state_changed = True
        auth._cache.serialize = MagicMock(return_value='{"cached": true}')
        (tmp_path / "token_cache.json").mkdir()
        return auth

    def test_a_failed_write_raises_a_named_auth_error(self, unwritable, tmp_path):
        with pytest.raises(TokenCacheError) as excinfo:
            unwritable.get_token()

        assert str(tmp_path / "token_cache.json") in str(excinfo.value)

    def test_a_failed_read_raises_it_too(self, mock_app_cls, auth_config, tmp_path):
        """`__init__` loads the cache, and first use of this class happens
        inside the retry envelope -- so the read has the same escape path."""
        (tmp_path / "token_cache.json").mkdir()

        with pytest.raises(TokenCacheError) as excinfo:
            DeviceCodeAuth(auth_config, TIMEOUT_SECONDS)

        assert str(tmp_path / "token_cache.json") in str(excinfo.value)

    def test_the_error_is_not_an_oserror(self):
        """The entire fix: every `except OSError` between the token provider
        and its caller would otherwise claim this as its own disk problem."""
        assert not issubclass(TokenCacheError, OSError)

    def test_the_error_is_not_transient(self):
        """`transient` makes the outbox put a failed intent back and try it
        again. A full disk or a misconfigured path is still true next pass."""
        assert getattr(TokenCacheError("boom"), "transient", False) is False

    def test_login_fails_the_same_way(self, unwritable):
        """`get_token`, `login` and `cached_token` all save the cache, which is
        why this is fixed at the I/O and not at the three call sites."""
        unwritable._app.initiate_device_flow.return_value = {"user_code": "A", "message": "go"}
        unwritable._app.acquire_token_by_device_flow.return_value = {"access_token": "t"}

        with pytest.raises(TokenCacheError):
            unwritable.login()

    def test_cached_token_fails_the_same_way(self, unwritable):
        with pytest.raises(TokenCacheError):
            unwritable.cached_token()
