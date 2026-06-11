"""Per-conversation message store — one JSON object per line (JSONL).

The store at ``<conv_dir>/messages.jsonl`` is the source of truth for a
conversation's full message history. ``messages.md`` is a derived artifact
rendered from it. Indexers in consuming workspaces scan ``*.md`` only, so
the sidecar is invisible to search and embedding pipelines.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields

from m365_extract.extractors.errors import MessageStoreError
from m365_extract.storage.base import StorageBackend


@dataclass(frozen=True)
class StoredMessage:
    """One Teams message (chat message, channel root, or channel reply)."""

    id: str
    parent_id: str | None  # None for chat messages and channel roots
    sender: str
    created: str  # ISO 8601 from Graph createdDateTime
    last_modified: str  # Graph lastModifiedDateTime
    etag: str
    edited: bool  # lastEditedDateTime != null
    deleted: bool  # deletedDateTime != null
    content: str  # rendered markdown (html→md already applied)
    attachments: list[dict]  # serialized AttachmentRef dicts
    subject: str | None  # channel root subject; None for chats and replies


_FIELD_NAMES = frozenset(f.name for f in fields(StoredMessage))


def sort_key(msg: StoredMessage) -> tuple[str, str]:
    """Canonical ``(created, id)`` message ordering.

    Store files, the markdown renderer, and the extractors' last-message-time
    derivation all sort with this one key.
    """
    return (msg.created, msg.id)


def load_store(storage: StorageBackend, path: str) -> dict[str, StoredMessage]:
    """Load a message store. Returns ``{}`` when the file does not exist.

    Raises ``MessageStoreError`` on any corrupt line — the caller may delete
    the store to force a backfill, but the code never silently skips data.
    """
    if not storage.file_exists(path):
        return {}

    store: dict[str, StoredMessage] = {}
    # Split on "\n" only — json.dumps escapes "\n" inside strings, but leaves
    # Unicode line separators (U+2028/U+2029) raw, which str.splitlines splits on.
    for line_no, line in enumerate(storage.read_file(path).split("\n"), start=1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise MessageStoreError(
                f"Corrupt JSON in message store {path} line {line_no}: {exc}. "
                "Delete the store file to force a backfill."
            ) from exc
        if not isinstance(obj, dict) or set(obj) != _FIELD_NAMES:
            raise MessageStoreError(
                f"Invalid message record in store {path} line {line_no}: "
                f"expected object with fields {sorted(_FIELD_NAMES)}. "
                "Delete the store file to force a backfill."
            )
        msg = StoredMessage(**obj)
        store[msg.id] = msg
    return store


def save_store(storage: StorageBackend, path: str, store: dict[str, StoredMessage]) -> None:
    """Write the store as JSONL, sorted by ``(created, id)`` for deterministic output."""
    ordered = sorted(store.values(), key=sort_key)
    lines = [json.dumps(asdict(m), ensure_ascii=False, sort_keys=True) for m in ordered]
    storage.write_file(path, "\n".join(lines) + "\n" if lines else "")


def merge_messages(
    store: dict[str, StoredMessage], fetched: list[StoredMessage]
) -> tuple[dict[str, StoredMessage], bool]:
    """Upsert fetched messages into the store by id. Pure function.

    Replaces an existing message only when its ``etag`` differs. Returns
    ``(new_store, changed)``; the input store is never mutated.
    """
    new_store = dict(store)
    changed = False
    for msg in fetched:
        existing = new_store.get(msg.id)
        if existing is None or existing.etag != msg.etag:
            new_store[msg.id] = msg
            changed = True
    return new_store, changed
