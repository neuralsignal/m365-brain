"""The MSAL/`requests` boundary: a timeout on every call, one exception type out.

Both properties here are about a transport the rest of the package cannot see.
`pytest_httpx` covers Graph and covers none of this -- MSAL uses `requests` --
so a regression in either would be invisible to every other test in the suite
right up until a laptop woke on a dead network.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests
from pydantic import SecretStr

from m365_brain.config import AuthConfig
from m365_brain.m365.auth.auth_code import AuthCodeAuth
from m365_brain.m365.auth.device_code import DeviceCodeAuth
from m365_brain.m365.auth.msal_http import TimeoutSession, auth_transport_errors
from m365_brain.m365.errors import AuthTransportError

TIMEOUT_SECONDS = 17


@pytest.fixture()
def auth_config(tmp_path):
    return AuthConfig(
        client_id="test-client-id",
        tenant_id="test-tenant-id",
        scopes=["User.Read", "offline_access"],
        token_cache_path=str(tmp_path / "token_cache.json"),
        client_secret=None,
    )


class TestTimeoutSession:
    def test_supplies_the_configured_timeout(self):
        """MSAL passes no `timeout`, and `requests` without one waits forever."""
        session = TimeoutSession(TIMEOUT_SECONDS)

        with patch.object(requests.Session, "request") as underlying:
            session.request("GET", "https://login.microsoftonline.com/common")

        assert underlying.call_args.kwargs["timeout"] == TIMEOUT_SECONDS

    def test_an_explicit_timeout_still_wins(self):
        session = TimeoutSession(TIMEOUT_SECONDS)

        with patch.object(requests.Session, "request") as underlying:
            session.request("GET", "https://login.microsoftonline.com/common", timeout=1)

        assert underlying.call_args.kwargs["timeout"] == 1

    def test_msal_receives_a_session_carrying_the_timeout(self, auth_config):
        """The wiring, not just the class: `graph.timeout_seconds` has to arrive.

        MSAL calls the session it is handed -- through a throttling decorator
        that delegates `get`/`post` to it -- rather than building its own, so
        what arrives here is what will carry the timeout on the wire.
        """
        with patch("m365_brain.m365.auth.device_code.msal.PublicClientApplication") as app_cls:
            DeviceCodeAuth(auth_config, TIMEOUT_SECONDS)

        http_client = app_cls.call_args.kwargs["http_client"]
        assert isinstance(http_client, requests.Session)
        with patch.object(requests.Session, "request") as underlying:
            http_client.request("GET", "https://login.microsoftonline.com/common")
        assert underlying.call_args.kwargs["timeout"] == TIMEOUT_SECONDS


class TestTranslation:
    def test_requests_transport_fault_becomes_auth_transport_error(self):
        with pytest.raises(AuthTransportError, match="ConnectionError") as excinfo, auth_transport_errors():
            raise requests.exceptions.ConnectionError("getaddrinfo failed")

        assert isinstance(excinfo.value.__cause__, requests.exceptions.ConnectionError)

    def test_is_not_a_graph_api_error(self):
        """The inheritance choice is load-bearing, so it is asserted.

        Twelve per-item handlers in the extractors catch `GraphApiError` to
        survive one unreadable item. An unreachable identity provider must not
        be survivable that way -- it would skip every remaining item and then
        report a successful sync.
        """
        from m365_brain.m365.errors import GraphApiError

        assert not issubclass(AuthTransportError, GraphApiError)

    def test_a_plain_os_error_is_not_translated(self):
        """`RequestException` subclasses `OSError`, so the catch must not widen.

        Catching `OSError` would have been the shorter spelling and would also
        have swallowed the token-cache read and write either side of these
        calls, reporting a full disk as an unreachable identity provider.
        """
        with pytest.raises(OSError, match="token cache"), auth_transport_errors():
            raise OSError("cannot read token cache")

    def test_a_transport_fault_building_the_app_is_translated(self, auth_config):
        """MSAL performs authority discovery in its constructor.

        First use of a profile therefore builds the app *inside* `GraphClient`'s
        retry loop, so a dead identity provider has to arrive there as
        `AuthTransportError` like any other token failure. Retrying is safe
        because nothing holds the instance until `__init__` returns.
        """
        with (
            patch(
                "m365_brain.m365.auth.device_code.msal.PublicClientApplication",
                side_effect=requests.exceptions.ConnectionError("getaddrinfo failed"),
            ),
            pytest.raises(AuthTransportError, match="ConnectionError"),
        ):
            DeviceCodeAuth(auth_config, TIMEOUT_SECONDS)

    def test_a_transport_fault_building_the_confidential_app_is_translated(self, auth_config):
        """The web flow's app has the same constructor-does-network property.

        `ConfidentialClientApplication` runs the same authority discovery as its
        public sibling, and `make_web_token_provider` builds it per request, so
        leaving this path untranslated would reopen the gap on the multi-user
        side only -- the half with no interactive user to notice.
        """
        config = auth_config.model_copy(update={"client_secret": SecretStr("test-secret")})
        with (
            patch(
                "m365_brain.m365.auth.auth_code.msal.ConfidentialClientApplication",
                side_effect=requests.exceptions.ConnectionError("getaddrinfo failed"),
            ),
            pytest.raises(AuthTransportError, match="ConnectionError"),
        ):
            AuthCodeAuth(config, TIMEOUT_SECONDS)

    def test_a_transport_fault_listing_accounts_is_translated(self, auth_config):
        """`get_accounts()` looks like a cache read and is not one.

        On a miss MSAL falls through to instance discovery over `requests`, so
        the account lookup that opens a silent refresh can fault exactly like
        the refresh it precedes.
        """
        with patch("m365_brain.m365.auth.device_code.msal.PublicClientApplication") as app_cls:
            app = MagicMock()
            app_cls.return_value = app
            app.get_accounts.side_effect = requests.exceptions.ConnectionError("getaddrinfo failed")

            auth = DeviceCodeAuth(auth_config, TIMEOUT_SECONDS)
            with pytest.raises(AuthTransportError):
                auth.get_token()

    def test_msal_error_dicts_pass_through_untouched(self, auth_config):
        """A revoked consent is not transient; it must not be retried.

        Through `login`, because that is now the only route to the device
        flow -- `get_token` raises `AuthRequiredError` before reaching it. The
        claim under test is unchanged: MSAL's own error dict is not laundered
        into an `AuthTransportError` and so never looks retryable.
        """
        with patch("m365_brain.m365.auth.device_code.msal.PublicClientApplication") as app_cls:
            app = MagicMock()
            app_cls.return_value = app
            app.initiate_device_flow.return_value = {"user_code": "A", "message": "go"}
            app.acquire_token_by_device_flow.return_value = {"error": "invalid_grant"}

            auth = DeviceCodeAuth(auth_config, TIMEOUT_SECONDS)
            with pytest.raises(SystemExit):
                auth.login()

    def test_a_dns_failure_during_silent_refresh_is_translated(self, auth_config):
        """The exact 2026-08-25 shape: DNS dies while refreshing a cached token."""
        with patch("m365_brain.m365.auth.device_code.msal.PublicClientApplication") as app_cls:
            app = MagicMock()
            app_cls.return_value = app
            app.get_accounts.return_value = [{"username": "user@example.com"}]
            app.acquire_token_silent.side_effect = requests.exceptions.ConnectionError(
                "[Errno 8] nodename nor servname provided"
            )

            auth = DeviceCodeAuth(auth_config, TIMEOUT_SECONDS)
            with pytest.raises(AuthTransportError):
                auth.get_token()
