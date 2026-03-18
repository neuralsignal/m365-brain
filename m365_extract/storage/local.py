"""Local filesystem storage backend."""

from __future__ import annotations

from pathlib import Path

from m365_extract.storage.exceptions import PathTraversalError


class LocalBackend:
    """Stores files on the local filesystem under a base directory."""

    def __init__(self, base_path: str) -> None:
        self._base = Path(base_path).resolve()
        self._base.mkdir(parents=True, exist_ok=True)

    def _safe_resolve(self, path: str) -> Path:
        """Resolve a relative path and verify it stays within the base directory."""
        full = (self._base / path).resolve()
        if not full.is_relative_to(self._base):
            raise PathTraversalError(f"Path traversal detected: {path!r} resolves outside base directory")
        return full

    def write_file(self, path: str, content: str) -> None:
        full = self._safe_resolve(path)
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")

    def read_file(self, path: str) -> str:
        full = self._safe_resolve(path)
        return full.read_text(encoding="utf-8")

    def file_exists(self, path: str) -> bool:
        return self._safe_resolve(path).exists()

    def list_files(self, prefix: str) -> list[str]:
        target = self._safe_resolve(prefix)
        if not target.exists():
            return []
        results: list[str] = []
        if target.is_file():
            results.append(prefix)
        else:
            for p in target.rglob("*"):
                if p.is_file():
                    results.append(str(p.relative_to(self._base)))
        return sorted(results)

    def delete_file(self, path: str) -> None:
        full = self._safe_resolve(path)
        if full.exists():
            full.unlink()
            # Clean up empty parent directories
            parent = full.parent
            while parent != self._base:
                if not any(parent.iterdir()):
                    parent.rmdir()
                    parent = parent.parent
                else:
                    break
