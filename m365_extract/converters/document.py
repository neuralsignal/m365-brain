"""Document conversion via obsidian-import.

Thin wrapper that converts files (DOCX, PDF, PPTX, etc.) to markdown text.
obsidian-import is imported at call time so the package remains optional.
"""

from __future__ import annotations

from pathlib import Path


def convert_document(
    file_path: Path,
    converters_config: dict,
) -> str:
    """Convert a file to markdown text using obsidian-import.

    converters_config is a raw dict matching obsidian-import's YAML structure.
    It is merged with obsidian-import's bundled defaults so only overrides are needed.

    Raises ImportError if obsidian-import is not installed.
    """
    from obsidian_import import extract_text
    from obsidian_import.config import _build_config, _deep_merge, _load_default_yaml

    defaults = _load_default_yaml()
    merged = _deep_merge(defaults, converters_config)
    config = _build_config(merged, config_dir=None)
    return extract_text(file_path, config)
