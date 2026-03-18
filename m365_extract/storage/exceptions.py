"""Storage-specific exceptions."""

from __future__ import annotations


class StorageError(Exception):
    """Raised when a storage operation fails."""


class PathTraversalError(StorageError):
    """Raised when a path escapes the allowed base directory."""
