"""Teams channel extractor — merge-based incremental sync of channel threads.

Uses the non-delta list endpoint ``/teams/{tid}/channels/{cid}/messages`` with
``$expand=replies`` (the delta endpoint is undocumented, flaky, and never
returns replies). The response is sorted by last-modified of the entire reply
chain, descending, which enables early-stop paging against a per-channel
watermark ``{team_id}:{channel_id}`` kept in extractor state.

Each channel is a folder ``teams-channels/<team-slug>/<channel-slug>-<hash6>/``
containing ``messages.jsonl`` (source of truth), ``messages.md`` (derived),
and ``attachments/`` / ``attachments_converted/`` beside them.

Channel selection: ``channels: null`` is discovery mode (walks
``/me/joinedTeams`` + ``/teams/{id}/channels``; additionally requires the
``Team.ReadBasic.All`` + ``Channel.ReadBasic.All`` delegated scopes), while an
explicit ``channels`` list iterates the configured entries with no discovery
calls at all, so the ``required_scopes`` minimum below is sufficient.
"""

from __future__ import annotations

from datetime import UTC, datetime

import httpx
import structlog

from m365_extract.config import TeamsChannelsExtractorConfig
from m365_extract.extractors._message_renderer import render_channel_body
from m365_extract.extractors._message_store import (
    StoredMessage,
    load_store,
    merge_messages,
    save_store,
    sort_key,
)
from m365_extract.extractors._teams_channel_targets import discover_targets, explicit_targets
from m365_extract.extractors._teams_ingest import GRAPH_PAGE_SIZE, is_etag_fresh, to_stored_message
from m365_extract.extractors.errors import MessageStoreError
from m365_extract.frontmatter import TeamsChannelData, build_teams_channel_frontmatter
from m365_extract.graph_client import GraphApiError, GraphClient
from m365_extract.markdown_writer import dumps_markdown, short_hash, slugify
from m365_extract.storage.base import StorageBackend

log = structlog.get_logger()

name = "teams_channels"
required_scopes = ["ChannelMessage.Read.All", "Files.Read.All"]


def run(
    client: GraphClient,
    storage: StorageBackend,
    state: dict,
    config: TeamsChannelsExtractorConfig,
    converters_config: dict,
) -> tuple[dict, int]:
    """Extract Teams channel messages. Returns (updated_state, items_written)."""
    state.setdefault("watermarks", {})
    state.setdefault("history_complete", {})
    state.setdefault("failed_attachments", {})
    for stale_key in [key for key in state if key.startswith("delta_")]:
        del state[stale_key]

    if config.channels is None:
        targets = discover_targets(client)
    else:
        targets = explicit_targets(config.channels)

    written = 0
    for team_id, team_name, channel in targets:
        if _process_channel(client, storage, team_id, team_name, channel, state, config, converters_config):
            written += 1

    state["last_sync"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    state["channels_written"] = written
    log.info("teams_channels.sync_complete", written=written)
    return state, written


def _chain_modified(root: dict, replies: list[dict]) -> str:
    """Max lastModifiedDateTime across a root message and all its replies."""
    times = [root.get("lastModifiedDateTime") or root.get("createdDateTime", "")]
    times.extend(r.get("lastModifiedDateTime") or r.get("createdDateTime", "") for r in replies)
    return max(times)


def _fetch_chains(
    client: GraphClient,
    team_id: str,
    channel_id: str,
    watermark: str | None,
    max_messages: int,
) -> tuple[list[tuple[dict, list[dict]]], bool]:
    """Fetch (root, replies) chains in chain-modified-descending order.

    Stops paging as soon as a chain at or below the watermark appears (the
    server sort guarantees everything after it is older). During backfill
    (no watermark), stops once ``max_messages`` total messages are collected.
    Returns ``(chains, truncated_by_cap)``.
    """
    url: str | None = f"/teams/{team_id}/channels/{channel_id}/messages"
    params: dict = {"$top": str(GRAPH_PAGE_SIZE), "$expand": "replies"}
    chains: list[tuple[dict, list[dict]]] = []
    total = 0
    truncated = False
    first_page = True

    while url:
        data = client.get(url, params=params if first_page else None)
        first_page = False
        stop = False
        for root in data.get("value", []):
            replies = list(root.get("replies") or [])
            next_link = root.get("replies@odata.nextLink")
            while next_link:
                reply_page = client.get(next_link, params=None)
                replies.extend(reply_page.get("value", []))
                next_link = reply_page.get("@odata.nextLink")

            if watermark and _chain_modified(root, replies) <= watermark:
                stop = True
                break
            chains.append((root, replies))
            total += 1 + len(replies)
            if watermark is None and total >= max_messages:
                truncated = True
                stop = True
                break
        if stop:
            break
        url = data.get("@odata.nextLink")

    return chains, truncated


def _convert_chains(
    client: GraphClient,
    storage: StorageBackend,
    chains: list[tuple[dict, list[dict]]],
    store: dict[str, StoredMessage],
    base: str,
    conv_dir: str,
    config: TeamsChannelsExtractorConfig,
    converters_config: dict,
    failed_attachments: dict[str, str],
) -> list[StoredMessage]:
    """Convert fetched chains to StoredMessages, skipping fresh and non-message entries."""
    fetched: list[StoredMessage] = []
    for root, replies in chains:
        root_id = root.get("id", "")
        if root.get("messageType") == "message" and not is_etag_fresh(store.get(root_id), root):
            fetched.append(
                to_stored_message(
                    client,
                    storage,
                    root,
                    None,
                    f"{base}/{root_id}",
                    conv_dir,
                    config,
                    converters_config,
                    failed_attachments,
                    store.get(root_id),
                )
            )
        for reply in replies:
            reply_id = reply.get("id", "")
            if reply.get("messageType") != "message" or is_etag_fresh(store.get(reply_id), reply):
                continue
            fetched.append(
                to_stored_message(
                    client,
                    storage,
                    reply,
                    root_id,
                    f"{base}/{root_id}/replies/{reply_id}",
                    conv_dir,
                    config,
                    converters_config,
                    failed_attachments,
                    store.get(reply_id),
                )
            )
    return fetched


def _write_channel(
    storage: StorageBackend,
    team_name: str,
    channel_name: str,
    channel_id: str,
    store: dict[str, StoredMessage],
    conv_dir: str,
    history_complete: bool,
) -> None:
    """Render the store and write messages.md with frontmatter and skeleton."""
    ordered = sorted(store.values(), key=sort_key)
    last_message_time = ordered[-1].created if ordered else ""

    fm = build_teams_channel_frontmatter(
        TeamsChannelData(
            team_name=team_name,
            channel_name=channel_name,
            channel_id=channel_id,
            last_message_time=last_message_time,
            message_count=len(store),
            history_complete=history_complete,
        )
    )

    body_parts = [f"# {team_name} / {channel_name}\n"]
    body_parts.append("## Observations\n")
    body_parts.append(f"- [team] {team_name}")
    body_parts.append(f"- [channel] {channel_name}")
    body_parts.append(f"- [last_message_time] {last_message_time}")
    body_parts.append(f"- [message_count] {len(store)}")
    body_parts.append("\n---\n")
    body_parts.append("## Messages\n")
    body_parts.append(render_channel_body(store))

    storage.write_file(f"{conv_dir}/messages.md", dumps_markdown(fm, "\n".join(body_parts)))
    log.debug("teams_channels.wrote", team=team_name, channel=channel_name, messages=len(store))


def _safe_fetch_chains(
    client: GraphClient,
    team_id: str,
    channel_id: str,
    watermark: str | None,
    max_messages: int,
    team_name: str,
    channel_name: str,
) -> tuple[list[tuple[dict, list[dict]]], bool] | None:
    """Fetch message chains with per-channel error containment."""
    try:
        return _fetch_chains(client, team_id, channel_id, watermark, max_messages)
    except GraphApiError as exc:
        log.warning("teams_channels.fetch_failed", team=team_name, channel=channel_name, error=str(exc))
        return None
    except httpx.TransportError as exc:
        log.error("teams_channels.fetch_transport_error", team=team_name, channel=channel_name, error=str(exc))
        return None


def _load_and_convert(
    client: GraphClient,
    storage: StorageBackend,
    chains: list[tuple[dict, list[dict]]],
    store_path: str,
    base: str,
    conv_dir: str,
    config: TeamsChannelsExtractorConfig,
    converters_config: dict,
    failed_attachments: dict[str, str],
    team_name: str,
    channel_name: str,
) -> tuple[dict[str, StoredMessage], bool] | None:
    """Load message store, convert fetched chains, and merge."""
    try:
        store = load_store(storage, store_path)
    except MessageStoreError as exc:
        log.error(
            "teams_channels.store_corrupt", team=team_name, channel=channel_name, store=store_path, error=str(exc)
        )
        return None
    try:
        fetched = _convert_chains(
            client, storage, chains, store, base, conv_dir, config, converters_config, failed_attachments
        )
    except httpx.TransportError as exc:
        log.error("teams_channels.media_transport_error", team=team_name, channel=channel_name, error=str(exc))
        return None
    return merge_messages(store, fetched)


def _persist_channel_data(
    storage: StorageBackend,
    state: dict,
    key: str,
    watermark: str | None,
    chains: list[tuple[dict, list[dict]]],
    merged: dict[str, StoredMessage],
    changed: bool,
    store_path: str,
    conv_dir: str,
    team_name: str,
    channel_name: str,
    channel_id: str,
) -> bool:
    """Update watermark, persist store, write markdown."""
    new_watermark = max(_chain_modified(root, replies) for root, replies in chains)
    if new_watermark:
        state["watermarks"][key] = max(watermark or "", new_watermark)
    if changed or not storage.file_exists(store_path):
        save_store(storage, store_path, merged)
    if not changed:
        return False
    _write_channel(
        storage, team_name, channel_name, channel_id, merged, conv_dir, state["history_complete"].get(key, False)
    )
    return True


def _process_channel(
    client: GraphClient,
    storage: StorageBackend,
    team_id: str,
    team_name: str,
    channel: dict,
    state: dict,
    config: TeamsChannelsExtractorConfig,
    converters_config: dict,
) -> bool:
    """Process a single channel: fetch, merge, render. Returns True if written."""
    channel_id = channel.get("id", "")
    channel_name = channel.get("displayName", "General")
    key = f"{team_id}:{channel_id}"
    conv_dir = f"teams-channels/{slugify(team_name, 80)}/{slugify(channel_name, 80)}-{short_hash(channel_id, 6)}"
    store_path = f"{conv_dir}/messages.jsonl"

    watermark = state["watermarks"].get(key)
    if watermark and not storage.file_exists(store_path):
        log.warning("teams_channels.store_missing_backfill", team=team_name, channel=channel_name)
        watermark = None

    result = _safe_fetch_chains(
        client, team_id, channel_id, watermark, config.max_messages_per_channel, team_name, channel_name
    )
    if result is None:
        return False
    chains, truncated = result

    if watermark is None:
        state["history_complete"][key] = not truncated
    if not chains:
        return False

    merge_result = _load_and_convert(
        client,
        storage,
        chains,
        store_path,
        f"/teams/{team_id}/channels/{channel_id}/messages",
        conv_dir,
        config,
        converters_config,
        state["failed_attachments"],
        team_name,
        channel_name,
    )
    if merge_result is None:
        return False
    merged, changed = merge_result

    return _persist_channel_data(
        storage,
        state,
        key,
        watermark,
        chains,
        merged,
        changed,
        store_path,
        conv_dir,
        team_name,
        channel_name,
        channel_id,
    )
