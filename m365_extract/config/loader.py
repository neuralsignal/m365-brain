"""Config loader. Validates every key against the schema, fails fast on missing or mistyped values."""

from __future__ import annotations

import dataclasses
import os
import re
import sys
import types
from dataclasses import fields
from pathlib import Path
from typing import Union, get_args, get_origin, get_type_hints

import yaml

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
            _fail(f"environment variable '{var_name}' is not set")
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
# Validation / construction
# ---------------------------------------------------------------------------


def _is_optional(field_type: type) -> tuple[bool, type | None]:
    """Check if a type is Optional[X] (Union[X, None] or X | None). Returns (is_optional, inner_type)."""
    origin = get_origin(field_type)
    if origin is Union or isinstance(field_type, types.UnionType):
        args = get_args(field_type)
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1 and type(None) in args:
            return True, non_none[0]
    return False, None


def _build(cls: type, data: dict, path: str = "") -> object:
    """Recursively construct a dataclass from a dict, validating every key."""
    if not isinstance(data, dict):
        _fail(f"expected a mapping at '{path}', got {type(data).__name__}")

    resolved_hints = get_type_hints(cls)
    kwargs = {}
    for f in fields(cls):
        key = f.name
        full_path = f"{path}.{key}" if path else key
        field_type = resolved_hints[key]

        optional, inner_type = _is_optional(field_type)

        if key not in data:
            if optional:
                kwargs[key] = None
                continue
            _fail(f"missing key '{full_path}' (expected {_type_name(field_type)})")

        value = data[key]

        if value is None and optional:
            kwargs[key] = None
            continue

        # Use the inner type for Optional fields
        actual_type = inner_type if optional else field_type

        if dataclasses.is_dataclass(actual_type):
            kwargs[key] = _build(actual_type, value, full_path)
        else:
            _check_type(value, actual_type, full_path)
            kwargs[key] = value

    return cls(**kwargs)


def _check_type(value: object, expected: type, path: str) -> None:
    """Validate that value matches the expected type annotation."""
    origin = get_origin(expected)

    if origin is list:
        if not isinstance(value, list):
            _fail(f"'{path}' expected list, got {type(value).__name__}")
        args = get_args(expected)
        if args:
            item_type = args[0]
            for i, item in enumerate(value):
                if not isinstance(item, item_type):
                    _fail(f"'{path}[{i}]' expected {item_type.__name__}, got {type(item).__name__}")
        return

    if expected is bool:
        if not isinstance(value, bool):
            _fail(f"'{path}' expected bool, got {type(value).__name__}")
        return

    if expected is int:
        if isinstance(value, bool) or not isinstance(value, int):
            _fail(f"'{path}' expected int, got {type(value).__name__}")
        return

    if not isinstance(value, expected):
        _fail(f"'{path}' expected {_type_name(expected)}, got {type(value).__name__}")


def _type_name(t: type) -> str:
    """Human-readable name for a type annotation."""
    origin = get_origin(t)
    if origin is Union or isinstance(t, types.UnionType):
        args = get_args(t)
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1 and type(None) in args:
            return f"Optional[{_type_name(non_none[0])}]"
    if origin is list:
        args = get_args(t)
        if args:
            return f"list[{args[0].__name__}]"
        return "list"
    if hasattr(t, "__name__"):
        return t.__name__
    return str(t)


def _fail(message: str) -> None:
    """Print a config error and exit immediately."""
    print(f"Config error: {message}", file=sys.stderr)
    raise SystemExit(1)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


_PATH_KEYS = frozenset({"base_path", "state_file_path", "token_cache_path"})


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


def load_config(path: str) -> Config:
    """Load and validate config from a YAML file. Crashes on any error."""
    config_path = Path(path).resolve()
    if not config_path.exists():
        _fail(f"config file not found: {config_path}")

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        _fail(f"config file must contain a YAML mapping, got {type(raw).__name__}")

    expanded = _expand_env_recursive(raw)
    resolved = _resolve_paths(expanded, config_path.parent)
    return _build(Config, resolved)
