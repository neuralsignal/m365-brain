"""One markdown file in, one `Entity` out.

This is the only module that knows a document is a *file*. Everything above it
works on `Entity`, which is why the index never learns what a path is.

Frontmatter promotion is the subtle part. A key that is not structural and
holds a scalar becomes a synthetic observation, so that a property written in
YAML (`status: in_progress`) is searchable exactly like one written in the body
(`- [status] in_progress`). Which keys count as structural is
`index.frontmatter.structural_keys` -- required config with no library default,
because that list is a property of the corpus's own conventions. Shipping one
would mean shipping one author's frontmatter vocabulary and silently changing
observation counts for everybody else.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from m365_brain.config.index import IndexConfig, IndexRoot
from m365_brain.model import Entity, Observation
from m365_brain.parsers.frontmatter import extract_tags, parse_frontmatter
from m365_brain.parsers.observations import parse_observations
from m365_brain.parsers.relations import parse_relations
from m365_brain.parsers.text import file_checksum, slugify

_TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def parse_markdown_file(path: Path, root: IndexRoot, config: IndexConfig) -> Entity | None:
    """Parse a file found under `root`. None when the file cannot be read.

    Unreadable is not fatal: a sync pass over thousands of files should report a
    permission error as one skipped file, not abandon the run. Anything past the
    read -- a path outside the root, a broken config -- still raises.
    """
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    meta, body = parse_frontmatter(text)
    frontmatter = config.frontmatter

    title = str(meta.get(frontmatter.title_key) or path.stem)
    entity_type = str(meta.get(frontmatter.type_key) or frontmatter.default_type)
    permalink = str(meta.get(frontmatter.permalink_key) or slugify(title))

    named_keys = {
        frontmatter.title_key,
        frontmatter.type_key,
        frontmatter.permalink_key,
        frontmatter.tags_key,
    }
    metadata = {key: _json_safe(value) for key, value in meta.items() if key not in named_keys}

    relative_path = path.relative_to(Path(root.path)).as_posix()
    created, updated = _timestamps(path)

    observations = parse_observations(body, config.observations)
    observations.extend(_promoted_observations(meta, observations, named_keys | set(frontmatter.structural_keys)))

    return Entity(
        key=f"{root.name}/{relative_path}",
        root_name=root.name,
        file_path=relative_path,
        title=title,
        entity_type=entity_type,
        permalink=permalink,
        tags=extract_tags(meta, frontmatter),
        aliases=_aliases(meta, config.frontmatter.aliases_key),
        content=f"{title}\n{body}",
        checksum=file_checksum(path),
        metadata=metadata,
        created_at=created,
        updated_at=updated,
        observations=observations,
        relations=parse_relations(body, config.relations),
    )


def _timestamps(path: Path) -> tuple[str, str]:
    stat = path.stat()
    created = dt.datetime.fromtimestamp(stat.st_ctime, tz=dt.UTC).strftime(_TIMESTAMP_FORMAT)
    updated = dt.datetime.fromtimestamp(stat.st_mtime, tz=dt.UTC).strftime(_TIMESTAMP_FORMAT)
    return created, updated


def _aliases(meta: dict[str, Any], key: str) -> list[str]:
    """Aliases are lifted out of `metadata` into a typed column.

    Every backend has to answer "find by alias" and only some can query inside
    a JSON blob, so this one key gets a column of its own.
    """
    raw = meta.get(key)
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, list):
        return [str(alias).strip() for alias in raw if alias is not None]
    return []


def _promoted_observations(
    meta: dict[str, Any],
    body_observations: list[Observation],
    blocked_keys: set[str],
) -> list[Observation]:
    """Scalar frontmatter keys that are not structural, as observations.

    A body observation of the same category wins: it was written by hand and may
    carry context and tags the YAML scalar cannot.
    """
    body_categories = {observation.category.lower() for observation in body_observations}
    promoted: list[Observation] = []
    for key, value in meta.items():
        if key in blocked_keys or value is None:
            continue
        if isinstance(value, dict | list):
            continue  # a structure is metadata, not a statement
        if key.lower() in body_categories:
            continue
        promoted.append(
            Observation(
                category=key,
                content=value if isinstance(value, str) else str(value),
                tags=[],
                context=None,
            )
        )
    return promoted


def _json_safe(value: Any) -> Any:
    """Coerce YAML-parsed values to JSON-serialisable ones.

    `safe_load` turns a bare `2026-02-24` into a `datetime.date`, which
    `json.dumps` refuses. The index stores metadata as JSON, so the coercion has
    to happen before it gets there, not at the point of the write.
    """
    if isinstance(value, dt.datetime | dt.date):
        return value.isoformat()
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_safe(inner) for key, inner in value.items()}
    return value
