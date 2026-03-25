"""Config loader. Validates every key against the pydantic schema, fails fast on missing or mistyped values."""

from __future__ import annotations

import os
import re
from pathlib import Path

import yaml
from pydantic import ValidationError

from m365_extract.config.errors import ConfigError
from m365_extract.config.schema import Config

# ---------------------------------------------------------------------------
# Environment variable expansion
# ---------------------------------------------------------------------------

_ENV_PATTERN = re.compile(r"\$\{([^}]+)\}")


def _expand_env_vars(value: str) -> str:
    """Expand ${VAR_NAME} references in a string. Crashes if the env var is not set."""

    def _replace(match: re.Match) -> str:
        var_name = match.group(1)
        env_value = os.environ.get(var_name)
        if env_value is None:
            raise ConfigError(f"environment variable '{var_name}' is not set")
        return env_value

    return _ENV_PATTERN.sub(_replace, value)


def _expand_env_recursive(data: object) -> object:
    """Recursively expand environment variables in all string values."""
    if isinstance(data, str):
        return _expand_env_vars(data)
    if isinstance(data, dict):
        return {k: _expand_env_recursive(v) for k, v in data.items()}
    if isinstance(data, list):
        return [_expand_env_recursive(item) for item in data]
    return data


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

_PATH_KEYS = frozenset({"base_path", "db_path", "state_file_path", "token_cache_path"})


def _resolve_paths(data: object, config_dir: Path) -> object:
    """Resolve relative path values against the config file's directory."""
    if isinstance(data, dict):
        result = {}
        for k, v in data.items():
            if k in _PATH_KEYS and isinstance(v, str) and not Path(v).is_absolute():
                result[k] = str((config_dir / v).resolve())
            else:
                result[k] = _resolve_paths(v, config_dir)
        return result
    if isinstance(data, list):
        return [_resolve_paths(item, config_dir) for item in data]
    return data


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_config(path: str) -> Config:
    """Load and validate config from a YAML file. Raises ConfigError on any error."""
    config_path = Path(path).resolve()
    if not config_path.exists():
        raise ConfigError(f"config file not found: {config_path}")

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ConfigError(f"config file must contain a YAML mapping, got {type(raw).__name__}")

    expanded = _expand_env_recursive(raw)
    resolved = _resolve_paths(expanded, config_path.parent)
    try:
        return Config.model_validate(resolved)
    except ValidationError as exc:
        raise ConfigError(str(exc)) from exc
