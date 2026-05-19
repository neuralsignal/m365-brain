"""Teams chat extractor — syncs 1:1 and group chat messages via Graph API.

Uses /me/chats for chat list and /me/chats/{id}/messages for messages.
Each chat is written as a folder ``teams-chats/<slug>_<hash>/`` containing:

- ``messages.md`` — the concatenated message timeline with frontmatter
- ``attachments/<msg-id>/<name>`` — raw bytes for file attachments and
  inline images
- ``attachments_converted/<msg-id>/<name>.md`` — converted-to-markdown
  text for attachments whose extension matches
  ``attachment_convert_extensions``

Inline-image ``<img src>`` URLs that point at Graph ``hostedContents`` are
rewritten to the local relative path before HTML→markdown conversion so the
rendered timeline links to the on-disk copy.
"""

from __future__ import annotations

from datetime import UTC, datetime

import structlog

from m365_extract.config import TeamsChatsExtractorConfig
from m365_extract.extractors._message_helpers import extract_content, extract_sender
from m365_extract.extractors._teams_attachment_helpers import (
    AttachmentRef,
    download_inline_images,
    download_message_attachments,
)
from m365_extract.frontmatter import TeamsChatData, build_teams_chat_frontmatter
from m365_extract.graph_client import GraphApiError, GraphClient
from m365_extract.markdown_writer import dumps_markdown, loads_markdown, short_hash, slugify
from m365_extract.storage.base import StorageBackend

log = structlog.get_logger()

name = "teams_chats"
required_scopes = ["Chat.Read", "Files.Read.All"]


def run(
    client: GraphClient,
    storage: StorageBackend,
    state: dict,
    config: TeamsChatsExtractorConfig,
    converters_config: dict,
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
        if _process_chat(client, storage, chat, last_sync_str, max_messages, config, converters_config):
            written += 1

    state["last_sync"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    state["chats_synced"] = len(chats)
    state["chats_written"] = written
    log.info("teams_chats.sync_complete", written=written, total=len(chats))
    return state, written


def _extract_chat_data(chat: dict, messages: list[dict], max_messages: int) -> tuple[TeamsChatData, list[dict], str]:
    """Extract chat frontmatter data, sort messages, and compute the chat directory."""
    chat_id = chat.get("id", "")
    chat_type = chat.get("chatType", "oneOnOne")
    topic = chat.get("topic") or ""

    participants = [m.get("displayName", "") for m in chat.get("members", []) if m.get("displayName", "")]
    title = topic if topic else ", ".join(sorted(participants)) if participants else "Chat"

    messages_sorted = sorted(messages, key=lambda m: m.get("createdDateTime", ""))
    last_message_time = messages_sorted[-1].get("createdDateTime", "") if messages_sorted else ""

    slug = slugify(title, 80)
    hsh = short_hash(chat_id, 6)
    chat_dir = f"teams-chats/{slug}_{hsh}"

    data = TeamsChatData(
        title=title,
        conversation_id=chat_id,
        conversation_type=chat_type,
        participants=participants,
        last_message_time=last_message_time,
        message_limit_reached=len(messages) >= max_messages,
    )
    return data, messages_sorted, chat_dir


def _render_attachment_links(refs: list[AttachmentRef]) -> str:
    """Render an inline-link line for attachments beneath a message body."""
    parts: list[str] = []
    for ref in refs:
        parts.append(f"[{ref.name}]({ref.relative_path})")
        if ref.converted_path is not None:
            parts.append(f"[{ref.name} (text)]({ref.converted_path})")
    return "**Attachments:** " + " · ".join(parts)


def _write_chat(
    client: GraphClient,
    storage: StorageBackend,
    data: TeamsChatData,
    messages: list[dict],
    chat_dir: str,
    config: TeamsChatsExtractorConfig,
    converters_config: dict,
) -> bool:
    """Build frontmatter and markdown body for a chat, then write to storage."""
    fm = build_teams_chat_frontmatter(data)

    body_parts = [f"# {data.title}\n"]

    body_parts.append("## Observations\n")
    body_parts.append(f"- [conversation_type] {data.conversation_type}")
    body_parts.append(f"- [participants] {', '.join(data.participants)}")
    body_parts.append(f"- [last_message_time] {data.last_message_time}")
    body_parts.append(f"- [message_count] {len(messages)}")

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

    for msg in messages:
        msg_type = msg.get("messageType", "")
        if msg_type == "systemEventMessage":
            continue

        if config.download_inline_images:
            hosted_map = download_inline_images(client, storage, data.conversation_id, msg, chat_dir, config)
        else:
            hosted_map = {}

        if config.download_attachments:
            attachment_refs = download_message_attachments(client, storage, msg, chat_dir, config, converters_config)
        else:
            attachment_refs = []

        sender_name = extract_sender(msg)
        created = msg.get("createdDateTime", "")
        content = extract_content(msg, hosted_map)

        timestamp_short = created[:16].replace("T", " ") if created else ""
        header = f"### {timestamp_short} -- {sender_name}\n" if sender_name else f"### {timestamp_short}\n"
        body_parts.append(header)
        if content:
            body_parts.append(content)
        if attachment_refs:
            body_parts.append(_render_attachment_links(attachment_refs))
        body_parts.append("")

    content_str = dumps_markdown(fm, "\n".join(body_parts))
    storage.write_file(f"{chat_dir}/messages.md", content_str)
    log.debug("teams_chats.wrote", title=data.title, messages=len(messages))
    return True


def _process_chat(
    client: GraphClient,
    storage: StorageBackend,
    chat: dict,
    last_sync: str | None,
    max_messages: int,
    config: TeamsChatsExtractorConfig,
    converters_config: dict,
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

    data, messages_sorted, chat_dir = _extract_chat_data(chat, messages, max_messages)
    file_path = f"{chat_dir}/messages.md"

    if data.message_limit_reached:
        log.warning("teams_chats.message_limit_reached", chat_id=chat_id, messages=len(messages), limit=max_messages)

    if storage.file_exists(file_path):
        try:
            existing_content = storage.read_file(file_path)
            existing_fm, _ = loads_markdown(existing_content)
            if existing_fm.get("last_message_time") == data.last_message_time:
                return False
        except (ValueError, KeyError) as exc:
            log.warning(
                "teams_chats.existing_file_parse_failed",
                file_path=file_path,
                error=str(exc),
            )

    return _write_chat(client, storage, data, messages_sorted, chat_dir, config, converters_config)
