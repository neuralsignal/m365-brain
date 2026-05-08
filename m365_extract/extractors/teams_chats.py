"""Teams chat extractor — syncs 1:1 and group chat messages via Graph API.

Uses /me/chats for chat list and /me/chats/{id}/messages for messages.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import structlog

from m365_extract.config import TeamsChatsExtractorConfig
from m365_extract.extractors._message_helpers import extract_content, extract_sender
from m365_extract.frontmatter import build_teams_chat_frontmatter
from m365_extract.graph_client import GraphApiError, GraphClient
from m365_extract.markdown_writer import dumps_markdown, loads_markdown, short_hash, slugify
from m365_extract.storage.base import StorageBackend

log = structlog.get_logger()

name = "teams_chats"
required_scopes = ["Chat.Read"]


def run(
    client: GraphClient,
    storage: StorageBackend,
    state: dict,
    config: TeamsChatsExtractorConfig,
) -> tuple[dict, int]:
    """Extract Teams chat messages.

    Returns (updated_state, items_written).
    """
    last_sync_str = state.get("last_sync")
    max_messages = config.max_messages_per_chat

    chats = list(
        client.get_paginated(
            "/me/chats",
            params={"$expand": "members", "$top": "50"},
            max_pages=client.max_pages,
        )
    )
    log.info("teams_chats.fetched_chats", count=len(chats))

    written = 0
    for chat in chats:
        if _process_chat(client, storage, chat, last_sync_str, max_messages):
            written += 1

    state["last_sync"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    state["chats_synced"] = len(chats)
    state["chats_written"] = written
    log.info("teams_chats.sync_complete", written=written, total=len(chats))
    return state, written


@dataclass(frozen=True)
class ChatData:
    """Extracted and normalized Teams chat metadata."""

    chat_id: str
    chat_type: str
    title: str
    participants: list[str]
    messages: list[dict]
    last_message_time: str
    message_limit_reached: bool
    file_path: str


def _extract_chat_data(chat: dict, messages: list[dict], max_messages: int) -> ChatData:
    """Extract and normalize chat metadata and sort messages chronologically."""
    chat_id = chat.get("id", "")
    chat_type = chat.get("chatType", "oneOnOne")
    topic = chat.get("topic") or ""

    participants = [m.get("displayName", "") for m in chat.get("members", []) if m.get("displayName", "")]
    title = topic if topic else ", ".join(sorted(participants)) if participants else "Chat"

    messages_sorted = sorted(messages, key=lambda m: m.get("createdDateTime", ""))
    last_message_time = messages_sorted[-1].get("createdDateTime", "") if messages_sorted else ""

    slug = slugify(title, 80)
    hsh = short_hash(chat_id, 6)

    return ChatData(
        chat_id=chat_id,
        chat_type=chat_type,
        title=title,
        participants=participants,
        messages=messages_sorted,
        last_message_time=last_message_time,
        message_limit_reached=len(messages) >= max_messages,
        file_path=f"teams-chats/{slug}_{hsh}.md",
    )


def _write_chat(storage: StorageBackend, data: ChatData) -> bool:
    """Build frontmatter and markdown body for a chat, then write to storage."""
    fm = build_teams_chat_frontmatter(
        title=data.title,
        conversation_id=data.chat_id,
        conversation_type=data.chat_type,
        participants=data.participants,
        last_message_time=data.last_message_time,
        message_limit_reached=data.message_limit_reached,
    )

    body_parts = [f"# {data.title}\n"]

    body_parts.append("## Observations\n")
    body_parts.append(f"- [conversation_type] {data.chat_type}")
    body_parts.append(f"- [participants] {', '.join(data.participants)}")
    body_parts.append(f"- [last_message_time] {data.last_message_time}")
    body_parts.append(f"- [message_count] {len(data.messages)}")

    relations = []
    for p_name in data.participants:
        contact_slug = slugify(p_name, 80)
        if contact_slug and contact_slug != "untitled" and len(contact_slug) > 5:
            relations.append(f"- participant [[contact-{contact_slug}]]")
    if relations:
        body_parts.append("\n## Relations\n")
        body_parts.extend(relations)

    body_parts.append("\n---\n")
    body_parts.append("## Messages\n")

    for msg in data.messages:
        sender_name = extract_sender(msg)
        created = msg.get("createdDateTime", "")
        content = extract_content(msg)
        msg_type = msg.get("messageType", "")

        if msg_type == "systemEventMessage":
            continue

        timestamp_short = created[:16].replace("T", " ") if created else ""
        header = f"### {timestamp_short} -- {sender_name}\n" if sender_name else f"### {timestamp_short}\n"
        body_parts.append(header)
        if content:
            body_parts.append(content)
        body_parts.append("")

    content_str = dumps_markdown(fm, "\n".join(body_parts))
    storage.write_file(data.file_path, content_str)
    log.debug("teams_chats.wrote", title=data.title, messages=len(data.messages))
    return True


def _process_chat(
    client: GraphClient,
    storage: StorageBackend,
    chat: dict,
    last_sync: str | None,
    max_messages: int,
) -> bool:
    """Process a single chat: fetch messages and write markdown. Returns True if written."""
    chat_id = chat.get("id", "")

    params: dict = {"$top": "50", "$orderby": "createdDateTime desc"}
    if last_sync:
        params["$filter"] = f"lastModifiedDateTime gt {last_sync}"

    try:
        messages = list(
            client.get_paginated(
                f"/me/chats/{chat_id}/messages",
                params=params,
                max_pages=max(1, max_messages // 50),
            )
        )
    except GraphApiError as exc:
        log.warning("teams_chats.fetch_failed", chat_id=chat_id, error=str(exc))
        return False

    if not messages:
        return False

    data = _extract_chat_data(chat, messages, max_messages)

    if data.message_limit_reached:
        log.warning("teams_chats.message_limit_reached", chat_id=chat_id, messages=len(messages), limit=max_messages)

    if storage.file_exists(data.file_path):
        try:
            existing_content = storage.read_file(data.file_path)
            existing_fm, _ = loads_markdown(existing_content)
            if existing_fm.get("last_message_time") == data.last_message_time:
                return False
        except (ValueError, KeyError) as exc:
            log.warning(
                "teams_chats.existing_file_parse_failed",
                file_path=data.file_path,
                error=str(exc),
            )

    return _write_chat(storage, data)
