"""Config loader — loads m365-extract Config from YAML.

Default config path: config.web.yaml in repo root. Override via M365_ADMIN_CONFIG env var.
Loads .env from repo root before first config load to ensure env vars are available.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

from m365_extract.config import Config, load_config

_repo_root = Path(__file__).resolve().parent.parent
_cached_config: Config | None = None
_config_path: str | None = None


def get_config() -> Config:
    """Load and return the m365-extract Config singleton.

    Config path defaults to config.web.yaml in repo root.
    Override via M365_ADMIN_CONFIG env var.
    Result is cached after first load.
    """
    global _cached_config, _config_path  # noqa: PLW0603
    if _cached_config is not None:
        return _cached_config

    # Load .env on every first-load to ensure env vars are available regardless
    # of import order or Reflex process management.
    load_dotenv(_repo_root / ".env", override=False)

    _config_path = os.environ.get("M365_ADMIN_CONFIG", str(_repo_root / "config.web.yaml"))
    _cached_config = load_config(_config_path)

    if _cached_config.web is None:
        msg = (
            f"config.web is None — the loaded config file ({_config_path}) "
            "has no 'web:' section. The admin dashboard requires config.web.yaml, "
            "not the CLI config. Set M365_ADMIN_CONFIG=./config.web.yaml in .env."
        )
        raise RuntimeError(msg)

    return _cached_config


def get_config_path() -> str | None:
    """Return the path of the loaded config file, or None if not yet loaded."""
    return _config_path


def reset_config() -> None:
    """Clear the cached config. Used by tests."""
    global _cached_config, _config_path  # noqa: PLW0603
    _cached_config = None
    _config_path = None
