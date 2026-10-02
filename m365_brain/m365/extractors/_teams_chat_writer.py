"""Rendering helpers for Teams chat markdown output."""

from __future__ import annotations

import structlog

from m365_brain.m365.extractors._message_renderer import render_chat_body
from m365_brain.m365.extractors._message_store import StoredMessage, sort_key
from m365_brain.m365.frontmatter import TeamsChatData, build_teams_chat_frontmatter, participant_relations
from m365_brain.m365.markdown_writer import dumps_markdown, short_hash, slugify
from m365_brain.storage.base import StorageBackend
from m365_brain.vault.paths import VaultPaths

log = structlog.get_logger()


def chat_title_and_dir(
    chat: dict,
    paths: VaultPaths,
    extractor_name: str,
) -> tuple[str, str, list[str]]:
    """Derive (title, chat_dir, participants) from a Graph chat object."""
    topic = chat.get("topic") or ""
    participants = [m.get("displayName", "") for m in chat.get("members", []) if m.get("displayName", "")]
    title = topic if topic else ", ".join(sorted(participants)) if participants else "Chat"
    chat_dir = paths.inbox_item(extractor_name, f"{slugify(title, 80)}_{short_hash(chat.get('id', ''), 6)}")
    return title, chat_dir, participants


def write_chat(
    storage: StorageBackend,
    chat: dict,
    store: dict[str, StoredMessage],
    chat_dir: str,
    history_complete: bool,
    paths: VaultPaths,
    extractor_name: str,
) -> str:
    """Render the store and write messages.md. Returns the written path."""
    title, _, participants = chat_title_and_dir(chat, paths, extractor_name)
    ordered = sorted(store.values(), key=sort_key)
    last_message_time = ordered[-1].created if ordered else ""

    data = TeamsChatData(
        title=title,
        conversation_id=chat.get("id", ""),
        conversation_type=chat.get("chatType", "oneOnOne"),
        participants=participants,
        last_message_time=last_message_time,
        message_count=len(store),
        history_complete=history_complete,
    )
    fm = build_teams_chat_frontmatter(data)

    body_parts = [f"# {title}\n"]
    body_parts.append("## Observations\n")
    body_parts.append(f"- [conversation_type] {data.conversation_type}")
    body_parts.append(f"- [last_message_time] {last_message_time}")
    body_parts.append(f"- [message_count] {len(store)}")

    relations = participant_relations(data)
    if relations:
        body_parts.append("\n## Relations\n")
        body_parts.extend(relations)

    body_parts.append("\n---\n")
    body_parts.append("## Messages\n")
    body_parts.append(render_chat_body(store))

    file_path = paths.conversation_file(chat_dir)
    storage.write_file(file_path, dumps_markdown(fm, "\n".join(body_parts)))
    log.debug("teams_chats.wrote", title=title, messages=len(store))
    return file_path
