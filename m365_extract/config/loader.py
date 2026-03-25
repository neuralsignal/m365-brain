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
# Deep merge
# ---------------------------------------------------------------------------


def _deep_merge(base: dict, override: dict) -> dict:
    """Deep merge two dicts. Override wins for scalars and lists; dicts merge recursively."""
    result = dict(base)
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_config(path: str) -> Config:
    """Load and validate config from one or more comma-separated YAML files.

    When multiple paths are given, files are loaded left-to-right and deep-merged.
    Dicts merge recursively; lists and scalars from later files override earlier ones.
    Relative paths in the config are resolved against the first file's parent directory.
    """
    paths = [p.strip() for p in path.split(",")]

    merged: dict = {}
    first_config_dir: Path | None = None

    for p in paths:
        config_path = Path(p).resolve()
        if not config_path.exists():
            raise ConfigError(f"config file not found: {config_path}")

        raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        if raw is None:
            continue  # empty or comment-only file
        if not isinstance(raw, dict):
            raise ConfigError(f"config file must contain a YAML mapping, got {type(raw).__name__}: {config_path}")

        merged = _deep_merge(merged, raw)
        if first_config_dir is None:
            first_config_dir = config_path.parent

    if not merged:
        raise ConfigError(f"no config data found in: {path}")

    expanded = _expand_env_recursive(merged)
    resolved = _resolve_paths(expanded, first_config_dir)
    try:
        return Config.model_validate(resolved)
    except ValidationError as exc:
        raise ConfigError(str(exc)) from exc
