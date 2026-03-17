"""Unified token provider interface.

Returns a callable that GraphClient can use to get tokens without knowing the auth flow.
"""

from __future__ import annotations

from collections.abc import Callable

from m365_extract.auth.device_code import DeviceCodeAuth
from m365_extract.config import AuthConfig


def make_cli_token_provider(auth_config: AuthConfig) -> Callable[[], str]:
    """Create a token provider for CLI mode (device code flow)."""
    auth = DeviceCodeAuth(auth_config)
    return auth.get_token
