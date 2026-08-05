"""Render a message store into the standardized day-grouped markdown timeline.

Pure functions, no I/O. Same store always produces identical output: messages
are sorted by ``(created, id)`` and all formatting is deterministic string
work on the ISO 8601 timestamps delivered by Graph (all times UTC).
"""

from __future__ import annotations

from m365_brain.extractors._message_store import StoredMessage, sort_key

_THREAD_TITLE_MAX_CHARS = 60
_TOMBSTONE_BODY = "*Message deleted.*"


def _day(msg: StoredMessage) -> str:
    return msg.created[:10]


def _time(msg: StoredMessage) -> str:
    return msg.created[11:16]


def _markers(msg: StoredMessage, orphaned: bool) -> str:
    suffix = ""
    if msg.deleted:
        suffix += " *(deleted)*"
    elif msg.edited:
        suffix += " *(edited)*"
    if orphaned:
        suffix += " *(orphaned reply)*"
    return suffix


def _attachment_line(attachments: list[dict]) -> str:
    parts: list[str] = []
    for att in attachments:
        parts.append(f"[{att['name']}]({att['relative_path']})")
        if att["converted_path"] is not None:
            parts.append(f"[{att['name']} (text)]({att['converted_path']})")
    return "**Attachments:** " + " · ".join(parts)


def _body_blocks(msg: StoredMessage) -> list[str]:
    blocks: list[str] = []
    if msg.deleted:
        blocks.append(_TOMBSTONE_BODY)
    elif msg.content:
        blocks.append(msg.content)
    if msg.attachments:
        blocks.append(_attachment_line(msg.attachments))
    return blocks


def _thread_title(root: StoredMessage) -> str:
    if root.subject:
        return root.subject
    for line in root.content.split("\n"):
        stripped = line.strip()
        if stripped:
            return stripped[:_THREAD_TITLE_MAX_CHARS]
    return "Thread"


def _header(msg: StoredMessage, title: str | None, orphaned: bool) -> str:
    header = f"### {_time(msg)}"
    if msg.sender:
        header += f" — {msg.sender}"
    if title is not None:
        header += f" — {title}"
    return header + _markers(msg, orphaned)


def _reply_header(reply: StoredMessage, root_day: str) -> str:
    timestamp = _time(reply) if _day(reply) == root_day else f"{_day(reply)} {_time(reply)}"
    header = f"#### ↳ {timestamp}"
    if reply.sender:
        header += f" — {reply.sender}"
    return header + _markers(reply, orphaned=False)


def _render_top_level(entries: list[tuple[StoredMessage, list[str]]]) -> str:
    """Render day-grouped blocks from (top-level message, its blocks) pairs."""
    parts: list[str] = []
    current_day = None
    for msg, blocks in entries:
        if _day(msg) != current_day:
            current_day = _day(msg)
            parts.append(f"## {current_day}")
        parts.extend(blocks)
    return "\n\n".join(parts)


def render_chat_body(store: dict[str, StoredMessage]) -> str:
    """Render a chat store as a flat day-grouped timeline."""
    entries: list[tuple[StoredMessage, list[str]]] = []
    for msg in sorted(store.values(), key=sort_key):
        entries.append((msg, [_header(msg, title=None, orphaned=False), *_body_blocks(msg)]))
    return _render_top_level(entries)


def render_channel_body(store: dict[str, StoredMessage]) -> str:
    """Render a channel store as a threaded day-grouped timeline.

    Threads render under their root's day. Replies whose root is missing from
    the store render top-level with an ``*(orphaned reply)*`` marker rather
    than being dropped (fail visible, not silent).
    """
    roots = [m for m in store.values() if m.parent_id is None]
    replies_by_root: dict[str, list[StoredMessage]] = {}
    orphans: list[StoredMessage] = []
    for msg in store.values():
        if msg.parent_id is None:
            continue
        if msg.parent_id in store:
            replies_by_root.setdefault(msg.parent_id, []).append(msg)
        else:
            orphans.append(msg)

    entries: list[tuple[StoredMessage, list[str]]] = []
    for root in roots:
        blocks = [_header(root, title=_thread_title(root), orphaned=False), *_body_blocks(root)]
        for reply in sorted(replies_by_root.get(root.id, []), key=sort_key):
            blocks.append(_reply_header(reply, _day(root)))
            blocks.extend(_body_blocks(reply))
        entries.append((root, blocks))
    for orphan in orphans:
        entries.append((orphan, [_header(orphan, title=None, orphaned=True), *_body_blocks(orphan)]))

    entries.sort(key=lambda pair: sort_key(pair[0]))
    return _render_top_level(entries)
