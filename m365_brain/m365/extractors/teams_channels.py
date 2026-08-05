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

from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
import structlog

from m365_brain.config import TeamsChannelsExtractorConfig
from m365_brain.m365.client import GraphApiError, GraphClient
from m365_brain.m365.extractors._message_renderer import render_channel_body
from m365_brain.m365.extractors._message_store import (
    StoredMessage,
    load_store,
    merge_messages,
    save_store,
    sort_key,
)
from m365_brain.m365.extractors._teams_channel_ingest import chain_modified, convert_chains, fetch_chains
from m365_brain.m365.extractors._teams_channel_targets import discover_targets, explicit_targets
from m365_brain.m365.extractors._teams_context import TeamsContext
from m365_brain.m365.extractors.base import ExtractorContext
from m365_brain.m365.extractors.errors import MessageStoreError
from m365_brain.m365.frontmatter import TeamsChannelData, build_teams_channel_frontmatter
from m365_brain.m365.markdown_writer import dumps_markdown, short_hash, slugify
from m365_brain.storage.base import StorageBackend
from m365_brain.vault.removal import PATH_MAP_STATE_KEY

log = structlog.get_logger()

name = "teams_channels"
required_scopes = ["ChannelMessage.Read.All", "Files.Read.All"]


@dataclass(frozen=True)
class ChannelInfo:
    """Identifies a channel for logging, frontmatter, and storage naming."""

    team_name: str
    channel_name: str
    channel_id: str


def run(
    client: GraphClient,
    storage: StorageBackend,
    state: dict,
    config: TeamsChannelsExtractorConfig,
    ctx: ExtractorContext,
) -> tuple[dict, int]:
    """Extract Teams channel messages. Returns (updated_state, items_written)."""
    state.setdefault("watermarks", {})
    state.setdefault("history_complete", {})
    state.setdefault("failed_attachments", {})
    path_map: dict[str, str] = state.setdefault(PATH_MAP_STATE_KEY, {})
    for stale_key in [key for key in state if key.startswith("delta_")]:
        del state[stale_key]

    if config.channels is None:
        targets = discover_targets(client)
    else:
        targets = explicit_targets(config.channels)

    written = 0
    for team_id, team_name, channel in targets:
        info = ChannelInfo(
            team_name=team_name,
            channel_name=channel.get("displayName", "General"),
            channel_id=channel.get("id", ""),
        )
        conv_dir = ctx.paths.inbox_item(
            name,
            slugify(info.team_name, 80),
            f"{slugify(info.channel_name, 80)}-{short_hash(info.channel_id, 6)}",
        )
        teams_ctx = TeamsContext(
            client=client,
            storage=storage,
            settings=config,
            converters_config=ctx.converters,
            failed_attachments=state["failed_attachments"],
            conv_dir=conv_dir,
            paths=ctx.paths,
        )
        if _process_channel(teams_ctx, info, team_id, state, config, path_map):
            written += 1

    state["last_sync"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    state["channels_written"] = written
    log.info("teams_channels.sync_complete", written=written)
    return state, written


def _write_channel(
    ctx: TeamsContext,
    info: ChannelInfo,
    store: dict[str, StoredMessage],
    history_complete: bool,
) -> str:
    """Render the store and write messages.md. Returns the written path."""
    ordered = sorted(store.values(), key=sort_key)
    last_message_time = ordered[-1].created if ordered else ""

    fm = build_teams_channel_frontmatter(
        TeamsChannelData(
            team_name=info.team_name,
            channel_name=info.channel_name,
            channel_id=info.channel_id,
            last_message_time=last_message_time,
            message_count=len(store),
            history_complete=history_complete,
        )
    )

    body_parts = [f"# {info.team_name} / {info.channel_name}\n"]
    body_parts.append("## Observations\n")
    body_parts.append(f"- [team] {info.team_name}")
    body_parts.append(f"- [channel] {info.channel_name}")
    body_parts.append(f"- [last_message_time] {last_message_time}")
    body_parts.append(f"- [message_count] {len(store)}")
    body_parts.append("\n---\n")
    body_parts.append("## Messages\n")
    body_parts.append(render_channel_body(store))

    file_path = ctx.paths.conversation_file(ctx.conv_dir)
    ctx.storage.write_file(file_path, dumps_markdown(fm, "\n".join(body_parts)))
    log.debug("teams_channels.wrote", team=info.team_name, channel=info.channel_name, messages=len(store))
    return file_path


def _safe_fetch_chains(
    ctx: TeamsContext,
    info: ChannelInfo,
    team_id: str,
    watermark: str | None,
    max_messages: int,
) -> tuple[list[tuple[dict, list[dict]]], bool] | None:
    """Fetch message chains with per-channel error containment.

    Returns None when the channel should be skipped without advancing its
    watermark, so the next cycle retries from the same point.
    """
    try:
        return fetch_chains(ctx.client, team_id, info.channel_id, watermark, max_messages)
    except GraphApiError as exc:
        log.warning("teams_channels.fetch_failed", team=info.team_name, channel=info.channel_name, error=str(exc))
        return None
    except httpx.TransportError as exc:
        log.error(
            "teams_channels.fetch_transport_error", team=info.team_name, channel=info.channel_name, error=str(exc)
        )
        return None


def _load_and_convert(
    ctx: TeamsContext,
    info: ChannelInfo,
    chains: list[tuple[dict, list[dict]]],
    base: str,
) -> tuple[dict[str, StoredMessage], bool] | None:
    """Load the message store, convert fetched chains, and merge the two.

    Returns None when the store is unreadable or media fetching fails.
    """
    store_path = ctx.paths.conversation_store(ctx.conv_dir)
    try:
        store = load_store(ctx.storage, store_path)
    except MessageStoreError as exc:
        log.error(
            "teams_channels.store_corrupt",
            team=info.team_name,
            channel=info.channel_name,
            store=store_path,
            error=str(exc),
        )
        return None
    try:
        fetched = convert_chains(ctx, chains, store, base)
    except httpx.TransportError as exc:
        log.error(
            "teams_channels.media_transport_error", team=info.team_name, channel=info.channel_name, error=str(exc)
        )
        return None
    return merge_messages(store, fetched)


def _advance_channel_watermark(
    state: dict,
    key: str,
    chains: list[tuple[dict, list[dict]]],
    watermark: str | None,
) -> None:
    """Advance the per-channel watermark to the max chain-modified time."""
    new_watermark = max(chain_modified(root, replies) for root, replies in chains)
    if new_watermark:
        state["watermarks"][key] = max(watermark or "", new_watermark)


def _persist_channel_data(
    ctx: TeamsContext,
    info: ChannelInfo,
    merged: dict[str, StoredMessage],
    changed: bool,
    history_complete: bool,
) -> str | None:
    """Persist the store and render markdown. Returns the written path, or None."""
    store_path = ctx.paths.conversation_store(ctx.conv_dir)
    if changed or not ctx.storage.file_exists(store_path):
        # Always persist the store once a watermark exists — even empty — so
        # file existence matches the watermark and backfill never re-triggers.
        save_store(ctx.storage, store_path, merged)
    if not changed:
        return None
    return _write_channel(ctx, info, merged, history_complete)


def _process_channel(
    ctx: TeamsContext,
    info: ChannelInfo,
    team_id: str,
    state: dict,
    config: TeamsChannelsExtractorConfig,
    path_map: dict[str, str],
) -> bool:
    """Process a single channel: fetch, merge, render. Returns True if written.

    Errors are contained per channel: a fetch/media/store failure skips this
    channel (without advancing its watermark) and the sync cycle continues.
    """
    key = f"{team_id}:{info.channel_id}"
    store_path = ctx.paths.conversation_store(ctx.conv_dir)

    watermark = state["watermarks"].get(key)
    if watermark and not ctx.storage.file_exists(store_path):
        log.warning("teams_channels.store_missing_backfill", team=info.team_name, channel=info.channel_name)
        watermark = None

    result = _safe_fetch_chains(ctx, info, team_id, watermark, config.max_messages_per_channel)
    if result is None:
        return False
    chains, truncated = result

    if watermark is None:
        state["history_complete"][key] = not truncated
    if not chains:
        return False

    merge_result = _load_and_convert(ctx, info, chains, f"/teams/{team_id}/channels/{info.channel_id}/messages")
    if merge_result is None:
        return False
    merged, changed = merge_result

    _advance_channel_watermark(state, key, chains, watermark)
    file_path = _persist_channel_data(ctx, info, merged, changed, state["history_complete"].get(key, False))
    if file_path is None:
        return False
    # No upstream removal signal exists for channels under delegated
    # permissions -- see CONTRACTS.md. The map still has to be kept: it is
    # what `vault purge` and any future signal use to find the file.
    path_map[key] = file_path
    return True
