"""Teams chat extractor — merge-based incremental sync of 1:1 and group chats.

Uses /me/chats for the chat list and /me/chats/{id}/messages for messages.
Each chat is a folder ``teams-chats/<slug>_<hash>/`` containing:

- ``messages.jsonl`` — per-chat message store, the source of truth
- ``messages.md`` — rendered day-grouped timeline (derived artifact)
- ``attachments/<msg-id>/<name>`` and ``attachments_converted/<msg-id>/<name>.md``

Incremental sync keys off a per-chat ``lastModifiedDateTime`` watermark kept
in extractor state. The Graph chat-messages endpoint applies ``$filter`` only
when ``$orderby`` targets the same property — the two are always paired here.
Fetched messages merge into the store, so history older than the current
fetch window is never lost. Incremental fetches are bounded by the global
``graph.max_pages``; if even that bound truncates the window, the watermark
is NOT advanced (loud refusal beats silent loss — the next cycle retries).
"""

from __future__ import annotations

import math
from datetime import UTC, datetime

import httpx
import structlog

from m365_extract.config import TeamsChatsExtractorConfig
from m365_extract.extractors._message_renderer import render_chat_body
from m365_extract.extractors._message_store import (
    StoredMessage,
    load_store,
    merge_messages,
    save_store,
    sort_key,
)
from m365_extract.extractors._teams_context import TeamsContext
from m365_extract.extractors._teams_ingest import GRAPH_PAGE_SIZE, is_etag_fresh, to_stored_message
from m365_extract.extractors.errors import MessageStoreError
from m365_extract.frontmatter import TeamsChatData, build_teams_chat_frontmatter
from m365_extract.graph_client import GraphApiError, GraphClient
from m365_extract.markdown_writer import dumps_markdown, short_hash, slugify
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
    """Extract Teams chat messages. Returns (updated_state, items_written)."""
    state.setdefault("watermarks", {})
    state.setdefault("history_complete", {})
    state.setdefault("failed_attachments", {})

    chats = list(
        client.get_paginated(
            "/me/chats",
            params={"$expand": "members", "$top": str(GRAPH_PAGE_SIZE)},
            max_pages=client.max_pages,
        )
    )
    log.info("teams_chats.fetched_chats", count=len(chats))

    written = 0
    for chat in chats:
        _, chat_dir, _ = _chat_title_and_dir(chat)
        ctx = TeamsContext(
            client=client,
            storage=storage,
            settings=config,
            converters_config=converters_config,
            failed_attachments=state["failed_attachments"],
            conv_dir=chat_dir,
        )
        if _process_chat(ctx, chat, state, config):
            written += 1

    state["last_sync"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    state["chats_synced"] = len(chats)
    state["chats_written"] = written
    log.info("teams_chats.sync_complete", written=written, total=len(chats))
    return state, written


def _chat_title_and_dir(chat: dict) -> tuple[str, str, list[str]]:
    """Derive (title, chat_dir, participants) from a Graph chat object."""
    topic = chat.get("topic") or ""
    participants = [m.get("displayName", "") for m in chat.get("members", []) if m.get("displayName", "")]
    title = topic if topic else ", ".join(sorted(participants)) if participants else "Chat"
    chat_dir = f"teams-chats/{slugify(title, 80)}_{short_hash(chat.get('id', ''), 6)}"
    return title, chat_dir, participants


def _write_chat(
    storage: StorageBackend,
    chat: dict,
    store: dict[str, StoredMessage],
    chat_dir: str,
    history_complete: bool,
) -> None:
    """Render the store and write messages.md with frontmatter and skeleton."""
    title, _, participants = _chat_title_and_dir(chat)
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
    body_parts.append(f"- [participants] {', '.join(participants)}")
    body_parts.append(f"- [last_message_time] {last_message_time}")
    body_parts.append(f"- [message_count] {len(store)}")

    relations = []
    for p_name in participants:
        contact_slug = slugify(p_name, 80)
        if contact_slug and contact_slug != "untitled" and len(contact_slug) > 5:
            relations.append(f"- participant [[contact-{contact_slug}]]")
    if relations:
        body_parts.append("\n## Relations\n")
        body_parts.extend(relations)

    body_parts.append("\n---\n")
    body_parts.append("## Messages\n")
    body_parts.append(render_chat_body(store))

    storage.write_file(f"{chat_dir}/messages.md", dumps_markdown(fm, "\n".join(body_parts)))
    log.debug("teams_chats.wrote", title=title, messages=len(store))


def _build_chat_fetch_params(
    watermark: str | None,
    config: TeamsChatsExtractorConfig,
    client_max_pages: int,
) -> tuple[dict, int]:
    """Build Graph API query params and page budget for a chat message fetch."""
    params: dict = {"$top": str(GRAPH_PAGE_SIZE)}
    if watermark:
        params["$orderby"] = "lastModifiedDateTime desc"
        params["$filter"] = f"lastModifiedDateTime gt {watermark}"
        return params, client_max_pages
    return params, max(1, math.ceil(config.max_messages_per_chat / GRAPH_PAGE_SIZE))


def _ingest_chat_messages(
    ctx: TeamsContext,
    store: dict[str, StoredMessage],
    fetched_raw: list[dict],
    chat_id: str,
) -> list[StoredMessage]:
    """Convert raw Graph messages to StoredMessages, skipping non-message and fresh entries."""
    fetched: list[StoredMessage] = []
    for msg in fetched_raw:
        if msg.get("messageType") != "message":
            continue
        prior = store.get(msg.get("id", ""))
        if is_etag_fresh(prior, msg):
            continue
        fetched.append(to_stored_message(ctx, msg, None, f"/chats/{chat_id}/messages/{msg.get('id', '')}", prior))
    return fetched


def _advance_chat_watermark(
    state: dict,
    chat_id: str,
    fetched_raw: list[dict],
    watermark: str | None,
    advance: bool,
) -> None:
    """Advance the per-chat watermark to the max lastModifiedDateTime in the fetch."""
    if not advance:
        return
    new_watermark = max(m.get("lastModifiedDateTime") or m.get("createdDateTime", "") for m in fetched_raw)
    if new_watermark:
        state["watermarks"][chat_id] = max(watermark or "", new_watermark)


def _process_chat(
    ctx: TeamsContext,
    chat: dict,
    state: dict,
    config: TeamsChatsExtractorConfig,
) -> bool:
    """Process a single chat: fetch, merge, render. Returns True if written.

    Errors are contained per chat: a fetch/media/store failure skips this chat
    (without advancing its watermark) and the sync cycle continues.
    """
    chat_id = chat.get("id", "")
    store_path = f"{ctx.conv_dir}/messages.jsonl"

    watermark = state["watermarks"].get(chat_id)
    if watermark and not ctx.storage.file_exists(store_path):
        log.warning("teams_chats.store_missing_backfill", chat_id=chat_id)
        watermark = None

    params, max_pages = _build_chat_fetch_params(watermark, config, ctx.client.max_pages)

    try:
        fetched_raw, truncated = ctx.client.get_pages(f"/me/chats/{chat_id}/messages", params, max_pages)
    except GraphApiError as exc:
        log.warning("teams_chats.fetch_failed", chat_id=chat_id, error=str(exc))
        return False
    except httpx.TransportError as exc:
        log.error("teams_chats.fetch_transport_error", chat_id=chat_id, error=str(exc))
        return False

    if watermark is None:
        state["history_complete"][chat_id] = not truncated

    if not fetched_raw:
        return False

    advance = watermark is None or not truncated
    if watermark is not None and truncated:
        log.error(
            "teams_chats.incremental_truncated",
            chat_id=chat_id,
            max_pages=max_pages,
            detail="watermark not advanced; the next cycle retries the window",
        )

    try:
        store = load_store(ctx.storage, store_path)
    except MessageStoreError as exc:
        log.error("teams_chats.store_corrupt", chat_id=chat_id, store=store_path, error=str(exc))
        return False

    try:
        fetched = _ingest_chat_messages(ctx, store, fetched_raw, chat_id)
    except httpx.TransportError as exc:
        log.error("teams_chats.media_transport_error", chat_id=chat_id, error=str(exc))
        return False

    merged, changed = merge_messages(store, fetched)
    _advance_chat_watermark(state, chat_id, fetched_raw, watermark, advance)

    if changed or not ctx.storage.file_exists(store_path):
        save_store(ctx.storage, store_path, merged)

    if not changed:
        return False
    _write_chat(ctx.storage, chat, merged, ctx.conv_dir, state["history_complete"].get(chat_id, False))
    return True
