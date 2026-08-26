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

from m365_brain.config import TeamsChatsExtractorConfig
from m365_brain.m365.client import GraphApiError, GraphClient
from m365_brain.m365.extractors._message_renderer import render_chat_body
from m365_brain.m365.extractors._message_store import (
    StoredMessage,
    load_store,
    merge_messages,
    save_store,
    sort_key,
)
from m365_brain.m365.extractors._teams_context import TeamsContext
from m365_brain.m365.extractors._teams_ingest import GRAPH_PAGE_SIZE, is_etag_fresh, to_stored_message
from m365_brain.m365.extractors.base import ExtractorContext
from m365_brain.m365.extractors.errors import MessageStoreError
from m365_brain.m365.frontmatter import TeamsChatData, build_teams_chat_frontmatter, participant_relations
from m365_brain.m365.markdown_writer import dumps_markdown, short_hash, slugify
from m365_brain.storage.base import StorageBackend
from m365_brain.vault.paths import VaultPaths
from m365_brain.vault.removal import PATH_MAP_STATE_KEY

log = structlog.get_logger()

name = "teams_chats"
required_scopes = ["Chat.Read", "Files.Read.All"]

# `chatType` values whose message collection cannot be read. `unknownFutureValue`
# is Graph's evolvable-enum sentinel, and on `/me/chats` it marks a tombstone: a
# roster entry for a thread the account has lost membership of, where the chat
# still answers 200 but `/messages` answers 403 InsufficientPrivileges. An
# externally-hosted meeting under an in-meeting-only chat policy joins this class
# the moment the meeting ends, so it grows, and the request has to not be made --
# the error-level `graph.request_failed` comes from the transport, not from here.
# Filtered statelessly rather than cached the way `failed_attachments` is: a
# recurring series is one thread id that is readable during each occurrence, so a
# negative cache keyed on it would blind the sync to every future occurrence.
_SKIPPED_CHAT_TYPES: frozenset[str] = frozenset({"unknownFutureValue"})


def run(
    client: GraphClient,
    storage: StorageBackend,
    state: dict,
    config: TeamsChatsExtractorConfig,
    ctx: ExtractorContext,
) -> tuple[dict, int]:
    """Extract Teams chat messages. Returns (updated_state, items_written)."""
    state.setdefault("watermarks", {})
    state.setdefault("history_complete", {})
    state.setdefault("failed_attachments", {})
    path_map: dict[str, str] = state.setdefault(PATH_MAP_STATE_KEY, {})

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
        if chat.get("chatType") in _SKIPPED_CHAT_TYPES:
            log.debug("teams_chats.chat_skipped_unreadable", chat_id=chat.get("id", ""))
            continue
        _, chat_dir, _ = _chat_title_and_dir(chat, ctx.paths)
        teams_ctx = TeamsContext(
            client=client,
            storage=storage,
            settings=config,
            converters_config=ctx.converters,
            failed_attachments=state["failed_attachments"],
            conv_dir=chat_dir,
            paths=ctx.paths,
        )
        result = _process_chat(teams_ctx, chat, state, config, path_map)
        if result is not None:
            written += 1
            ctx.recorder.note_records(*result)

    state["last_sync"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    state["chats_synced"] = len(chats)
    state["chats_written"] = written
    log.info("teams_chats.sync_complete", written=written, total=len(chats))
    return state, written


def _chat_title_and_dir(chat: dict, paths: VaultPaths) -> tuple[str, str, list[str]]:
    """Derive (title, chat_dir, participants) from a Graph chat object."""
    topic = chat.get("topic") or ""
    participants = [m.get("displayName", "") for m in chat.get("members", []) if m.get("displayName", "")]
    title = topic if topic else ", ".join(sorted(participants)) if participants else "Chat"
    chat_dir = paths.inbox_item(name, f"{slugify(title, 80)}_{short_hash(chat.get('id', ''), 6)}")
    return title, chat_dir, participants


def _write_chat(
    storage: StorageBackend,
    chat: dict,
    store: dict[str, StoredMessage],
    chat_dir: str,
    history_complete: bool,
    paths: VaultPaths,
) -> str:
    """Render the store and write messages.md. Returns the written path."""
    title, _, participants = _chat_title_and_dir(chat, paths)
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

    # Relation lines, not a joined observation: a participant is a counterparty
    # `ops tiers` counts, and one string holding all of them is one counterparty.
    # The 5-character slug filter that stood here dropped a short name silently.
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
    path_map: dict[str, str],
) -> tuple[str, list[str]] | None:
    """Process a single chat. Returns `(written path, merged ids)`, or None.

    Errors are contained per chat: a fetch/media/store failure skips this chat
    (without advancing its watermark) and the sync cycle continues.
    """
    chat_id = chat.get("id", "")
    store_path = ctx.paths.conversation_store(ctx.conv_dir)

    watermark = state["watermarks"].get(chat_id)
    if watermark and not ctx.storage.file_exists(store_path):
        log.warning("teams_chats.store_missing_backfill", chat_id=chat_id)
        watermark = None

    params, max_pages = _build_chat_fetch_params(watermark, config, ctx.client.max_pages)

    try:
        fetched_raw, truncated = ctx.client.get_pages(f"/me/chats/{chat_id}/messages", params, max_pages)
    except GraphApiError as exc:
        log.warning("teams_chats.fetch_failed", chat_id=chat_id, error=str(exc))
        return None
    except httpx.TransportError as exc:
        log.error("teams_chats.fetch_transport_error", chat_id=chat_id, error=str(exc))
        return None

    if watermark is None:
        state["history_complete"][chat_id] = not truncated

    if not fetched_raw:
        return None

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
        return None

    try:
        fetched = _ingest_chat_messages(ctx, store, fetched_raw, chat_id)
    except httpx.TransportError as exc:
        log.error("teams_chats.media_transport_error", chat_id=chat_id, error=str(exc))
        return None

    merged, merged_ids = merge_messages(store, fetched)
    _advance_chat_watermark(state, chat_id, fetched_raw, watermark, advance)

    if merged_ids or not ctx.storage.file_exists(store_path):
        save_store(ctx.storage, store_path, merged)

    if not merged_ids:
        return None
    file_path = _write_chat(
        ctx.storage, chat, merged, ctx.conv_dir, state["history_complete"].get(chat_id, False), ctx.paths
    )
    path_map[chat_id] = file_path
    # Recorded even though Graph offers chats no removal signal under delegated
    # permissions -- see CONTRACTS.md. The map is what a future signal, and
    # `vault purge` today, need in order to find the file again.
    return file_path, merged_ids
