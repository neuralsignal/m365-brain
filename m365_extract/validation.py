"""Input validation for values used in security-sensitive contexts."""

from __future__ import annotations

import re

from m365_extract.config.errors import ConfigError

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def validate_user_id(user_id: str) -> None:
    """Validate that user_id is a UUID (Entra OID format).

    Raises ConfigError if the format is invalid. Prevents path traversal
    when user_id is used as a filesystem path component.
    """
    if not _UUID_RE.match(user_id):
        raise ConfigError(f"Invalid user_id format (expected UUID): {user_id!r}")
