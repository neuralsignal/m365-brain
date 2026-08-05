"""Turning an absent optional section into one named crash.

Three subsystems each need "the config does not configure me" handling. One
function so they do not each invent a different message for the same fact.
"""

from __future__ import annotations

from m365_brain.config.errors import ConfigError


def require_section[T](section: T | None, name: str) -> T:
    """Return `section`, or raise naming the config key that is missing."""
    if section is None:
        raise ConfigError(f"config section '{name}:' is required for this operation but is absent from the config file")
    return section
