"""The `---` delimited YAML block at the top of a markdown file.

Malformed YAML is not an error here. A file whose frontmatter does not parse is
treated as a file with no frontmatter and its whole text as body: the corpus is
somebody's notes, not a build artifact, and refusing to index a document because
one key has an unclosed bracket loses more than it protects. Everything *else*
in this package fails loud; this is the one place where the input is expected to
be imperfect and the degradation is total rather than partial.
"""

from __future__ import annotations

from typing import Any

import yaml

from m365_brain.config.index import FrontmatterConfig


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Return `(metadata, body)`.

    `({}, text)` when there is no frontmatter block, when the block never
    closes, when the YAML does not parse, or when it parses to something other
    than a mapping.
    """
    text = text.lstrip("\ufeff")  # BOM: it would otherwise hide the `---`
    if not text.startswith("---"):
        return {}, text

    end = text.find("\n---", 3)
    if end == -1:
        return {}, text

    yaml_block = text[3:end].strip()
    body = text[end + 4 :].lstrip("\n")

    try:
        meta = yaml.safe_load(yaml_block)
    except yaml.YAMLError:
        return {}, text

    if not isinstance(meta, dict):
        return {}, text

    return _normalize_meta(meta), body


def _normalize_meta(meta: dict[str, Any]) -> dict[str, Any]:
    return {key: _normalize_value(value) for key, value in meta.items()}


def _normalize_value(value: Any) -> Any:
    """Stringify scalars so that `priority: 3` and `priority: "3"` index alike.

    Dates are deliberately left alone -- they survive to `_json_safe` in
    `document.py`, which knows the ISO format the index stores them in.
    """
    if value is None:
        return value
    if isinstance(value, bool):  # before int: bool is an int subclass
        return str(value)
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, dict):
        return {key: _normalize_value(inner) for key, inner in value.items()}
    if isinstance(value, list):
        return [_normalize_value(item) for item in value]
    return value


def extract_tags(meta: dict[str, Any], config: FrontmatterConfig) -> list[str]:
    """Tags as a list, whether the file wrote a YAML list or a comma-separated string.

    Both spellings are common in the wild and neither is worth rejecting a file over.
    """
    raw = meta.get(config.tags_key, [])
    if isinstance(raw, str):
        return [tag.strip() for tag in raw.split(",") if tag.strip()]
    if isinstance(raw, list):
        return [str(tag).strip() for tag in raw if tag is not None]
    return []
