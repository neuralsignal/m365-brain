"""Tests for m365_extract.auth.token_provider."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from m365_extract.auth.token_provider import make_cli_token_provider
from m365_extract.config import AuthConfig


def _auth_config() -> AuthConfig:
    return AuthConfig(
        client_id="test-client-id",
        tenant_id="test-tenant-id",
        scopes=["User.Read"],
        token_cache_path="/tmp/test_cache.json",
    )


@patch("m365_extract.auth.token_provider.DeviceCodeAuth")
def test_returns_callable(mock_device_code_auth: MagicMock) -> None:
    result = make_cli_token_provider(_auth_config())
    assert callable(result)


@patch("m365_extract.auth.token_provider.DeviceCodeAuth")
def test_returns_get_token_bound_method(mock_device_code_auth: MagicMock) -> None:
    mock_instance = MagicMock()
    mock_device_code_auth.return_value = mock_instance

    result = make_cli_token_provider(_auth_config())

    assert result is mock_instance.get_token


@patch("m365_extract.auth.token_provider.DeviceCodeAuth")
def test_passes_auth_config_to_device_code_auth(mock_device_code_auth: MagicMock) -> None:
    config = _auth_config()
    make_cli_token_provider(config)

    mock_device_code_auth.assert_called_once_with(config)
