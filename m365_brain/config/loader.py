"""Config loader. Validates every key against the pydantic schema, fails fast on missing or mistyped values."""

from __future__ import annotations

import os
import re
from pathlib import Path

import yaml
from pydantic import ValidationError

from m365_brain.config.errors import ConfigError
from m365_brain.config.schema import Config

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

# Keys whose string value is a filesystem path. A relative one resolves against
# the config file's directory, never the process CWD -- a daemon launched from
# `/` and a shell launched from the repo root must read the same database.
#
# `path` appears in `index.sqlite.path` and in every `index.roots[].path`,
# which is exactly the intent: a root path is as much a path as a database is.
_PATH_KEYS = frozenset(
    {
        "base_path",
        "db_path",
        "token_cache_path",
        "path",
        "root",
        "attachment_root",
        "html_path",
        "logo_path",
    }
)


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
        # The file path matters as much as the key path: with multi-file merge
        # an operator otherwise has to guess which of them owns the bad key.
        raise ConfigError(f"invalid config in {path}: {_render(exc)}") from exc


def _render(exc: ValidationError) -> str:
    """Format a validation error without echoing the input that failed.

    `str(ValidationError)` embeds `input_value`, and for a *model*-level error
    that input is the whole surrounding mapping -- so one missing or misspelt
    key next to a secret prints the secret. Pydantic truncates a long value to
    its first two and last seven characters, which hides the middle of a
    connection string and nothing at all of a short one.

    Dropping the input costs nothing diagnostically: pydantic puts the
    offending key in `loc`, not only in `input_value`, so "which key" and
    "what is wrong with it" both survive. What is lost is only the echo of a
    value the operator can read in their own file.
    """
    lines = [f"{len(exc.errors())} validation error(s) for {exc.title}"]
    for error in exc.errors(include_input=False, include_url=False):
        location = ".".join(str(part) for part in error["loc"]) or "(root)"
        lines.append(f"  {location}: {error['msg']}")
    return "\n".join(lines)
