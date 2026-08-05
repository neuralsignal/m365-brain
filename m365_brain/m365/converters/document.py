"""Document conversion via obsidian-import.

Thin wrapper that converts files (DOCX, PDF, PPTX, etc.) to markdown text.
obsidian-import is imported at call time so the package remains optional.
"""

from __future__ import annotations

from pathlib import Path


class DocumentConversionError(Exception):
    """Raised when obsidian-import fails to convert a document (timeout, bad format, missing backend)."""


def convert_document(
    file_path: Path,
    converters_config: dict,
) -> str:
    """Convert a file to markdown text using obsidian-import.

    converters_config is a raw dict matching obsidian-import's YAML structure.
    It is merged with obsidian-import's bundled defaults so only overrides are needed.

    Raises ImportError if obsidian-import is not installed and
    DocumentConversionError if the conversion itself fails.
    """
    from obsidian_import import config_from_overrides, extract_text
    from obsidian_import.exceptions import ObsidianImportError

    config = config_from_overrides(converters_config)
    try:
        return extract_text(file_path, config)
    except ObsidianImportError as exc:
        raise DocumentConversionError(f"conversion failed for {file_path.name}: {exc}") from exc
