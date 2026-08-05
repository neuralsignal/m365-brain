"""Storage backend protocol."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class StorageBackend(Protocol):
    """Protocol for storage backends. All paths are relative (e.g. 'emails/2026/03/subject.md')."""

    def write_file(self, path: str, content: str) -> None:
        """Write content to a file at the given relative path."""
        ...

    def read_file(self, path: str) -> str:
        """Read and return the content of a file at the given relative path."""
        ...

    def file_exists(self, path: str) -> bool:
        """Return True if a file exists at the given relative path."""
        ...

    def list_files(self, prefix: str) -> list[str]:
        """List all file paths under the given prefix."""
        ...

    def delete_file(self, path: str) -> None:
        """Delete a file at the given relative path."""
        ...

    def write_bytes(self, path: str, content: bytes) -> None:
        """Write binary content to a file at the given relative path."""
        ...
