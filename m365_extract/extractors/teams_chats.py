"""Teams chat extractor — syncs 1:1 and group chat messages via Graph API.

Uses /me/chats for chat list and /me/chats/{id}/messages for messages.
"""

from __future__ import annotations

from datetime import UTC, datetime

import structlog

from m365_extract.config import TeamsChatsExtractorConfig
from m365_extract.converters.html_to_md import html_to_markdown
from m365_extract.graph_client import GraphClient
from m365_extract.markdown_writer import (
    build_teams_chat_frontmatter,
    dumps_markdown,
    loads_markdown,
    short_hash,
    slugify,
)
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


def _process_chat(
    client: GraphClient,
    storage: StorageBackend,
    chat: dict,
    last_sync: str | None,
    max_messages: int,
) -> bool:
    """Process a single chat: fetch messages and write markdown. Returns True if written."""
    chat_id = chat.get("id", "")
    chat_type = chat.get("chatType", "oneOnOne")
    topic = chat.get("topic") or ""

    members = chat.get("members", [])
    participants = []
    for member in members:
        display_name = member.get("displayName", "")
        if display_name:
            participants.append(display_name)

    title = topic if topic else ", ".join(sorted(participants)) if participants else "Chat"

    # Fetch messages with optional incremental filter
    params: dict = {"$top": "50", "$orderby": "createdDateTime desc"}
    if last_sync:
        params["$filter"] = f"lastModifiedDateTime gt {last_sync}"

    try:
        messages = list(
            client.get_paginated(
                f"/me/chats/{chat_id}/messages",
                params=params,
                max_pages=max_messages // 50,
            )
        )
    except Exception as exc:
        log.warning("teams_chats.fetch_failed", chat_id=chat_id, error=str(exc))
        return False

    if not messages:
        return False

    # Sort chronologically
    messages.sort(key=lambda m: m.get("createdDateTime", ""))

    last_msg_time = messages[-1].get("createdDateTime", "") if messages else ""

    slug = slugify(title)
    hsh = short_hash(chat_id)
    file_path = f"teams-chats/{slug}_{hsh}.md"

    # Check if update is needed
    if storage.file_exists(file_path):
        try:
            existing_content = storage.read_file(file_path)
            existing_fm, _ = loads_markdown(existing_content)
            if existing_fm.get("last_message_time") == last_msg_time:
                return False
        except Exception as exc:
            log.warning(
                "teams_chats.existing_file_parse_failed",
                file_path=file_path,
                error=str(exc),
            )

    fm = build_teams_chat_frontmatter(
        title=title,
        conversation_id=chat_id,
        conversation_type=chat_type,
        participants=participants,
        last_message_time=last_msg_time,
    )

    body_parts = [f"# {title}\n"]

    # Observations
    body_parts.append("## Observations\n")
    body_parts.append(f"- [conversation_type] {chat_type}")
    body_parts.append(f"- [participants] {', '.join(participants)}")
    body_parts.append(f"- [last_message_time] {last_msg_time}")
    body_parts.append(f"- [message_count] {len(messages)}")

    # Relations
    relations = []
    for p_name in participants:
        contact_slug = slugify(p_name)
        if contact_slug and contact_slug != "untitled" and len(contact_slug) > 5:
            relations.append(f"- participant [[contact-{contact_slug}]]")
    if relations:
        body_parts.append("\n## Relations\n")
        body_parts.extend(relations)

    # Messages
    body_parts.append("\n---\n")
    body_parts.append("## Messages\n")

    for msg in messages:
        sender_name = _extract_sender(msg)
        created = msg.get("createdDateTime", "")
        content = _extract_content(msg)
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
    storage.write_file(file_path, content_str)
    log.debug("teams_chats.wrote", title=title, messages=len(messages))
    return True


def _extract_sender(msg: dict) -> str:
    """Extract the sender display name from a Graph chat message."""
    from_field = msg.get("from")
    if not from_field:
        return ""
    user = from_field.get("user")
    if user:
        return user.get("displayName", "")
    app = from_field.get("application")
    if app:
        return app.get("displayName", "Bot")
    return ""


def _extract_content(msg: dict) -> str:
    """Extract and convert message content to markdown."""
    body = msg.get("body", {})
    content_type = body.get("contentType", "text")
    content = body.get("content", "")

    if not content:
        return ""

    if content_type == "html":
        return html_to_markdown(content)
    return content
