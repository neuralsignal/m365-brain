"""Local filesystem storage backend."""

from __future__ import annotations

from pathlib import Path


class LocalBackend:
    """Stores files on the local filesystem under a base directory."""

    def __init__(self, base_path: str) -> None:
        self._base = Path(base_path)
        self._base.mkdir(parents=True, exist_ok=True)

    def write_file(self, path: str, content: str) -> None:
        full = self._base / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")

    def read_file(self, path: str) -> str:
        full = self._base / path
        return full.read_text(encoding="utf-8")

    def file_exists(self, path: str) -> bool:
        return (self._base / path).exists()

    def list_files(self, prefix: str) -> list[str]:
        target = self._base / prefix
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
        full = self._base / path
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
