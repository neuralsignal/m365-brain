"""Teams channel extractor — syncs channel messages via Graph API.

Uses /teams/{id}/channels and /teams/{id}/channels/{id}/messages.
Requires ChannelMessage.Read.All permission.
"""

from __future__ import annotations

from datetime import UTC, datetime

import structlog

from m365_extract.config import TeamsChannelsExtractorConfig
from m365_extract.extractors._message_helpers import extract_content, extract_sender
from m365_extract.frontmatter import TeamsChannelData, build_teams_channel_frontmatter
from m365_extract.graph_client import GraphApiError, GraphClient
from m365_extract.markdown_writer import dumps_markdown, short_hash, slugify
from m365_extract.storage.base import StorageBackend

log = structlog.get_logger()

name = "teams_channels"
required_scopes = ["ChannelMessage.Read.All"]


def run(
    client: GraphClient,
    storage: StorageBackend,
    state: dict,
    config: TeamsChannelsExtractorConfig,
) -> tuple[dict, int]:
    """Extract Teams channel messages.

    Returns (updated_state, items_written).
    """
    # Discover teams the user is a member of
    teams = list(client.get_paginated("/me/joinedTeams", params=None, max_pages=client.max_pages))
    log.info("teams_channels.fetched_teams", count=len(teams))

    written = 0
    for team in teams:
        team_id = team.get("id", "")
        team_name = team.get("displayName", "Unknown Team")

        channels = list(
            client.get_paginated(
                f"/teams/{team_id}/channels",
                params={"$top": "50"},
                max_pages=client.max_pages,
            )
        )

        for channel in channels:
            if _process_channel(client, storage, team_id, team_name, channel, state):
                written += 1

    state["last_sync"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    state["channels_written"] = written
    log.info("teams_channels.sync_complete", written=written)
    return state, written


def _process_channel(
    client: GraphClient,
    storage: StorageBackend,
    team_id: str,
    team_name: str,
    channel: dict,
    state: dict,
) -> bool:
    """Process a single channel. Returns True if written."""
    channel_id = channel.get("id", "")
    channel_name = channel.get("displayName", "General")

    # Use delta for channel messages
    delta_key = f"delta_{team_id}_{channel_id}"
    delta_link = state.get(delta_key)

    try:
        messages, new_delta_link = client.get_delta(
            f"/teams/{team_id}/channels/{channel_id}/messages/delta",
            delta_link,
            params={"$top": "50"},
            max_pages=client.max_pages,
        )
    except GraphApiError as exc:
        log.warning(
            "teams_channels.fetch_failed",
            team=team_name,
            channel=channel_name,
            error=str(exc),
        )
        return False

    if new_delta_link:
        state[delta_key] = new_delta_link

    if not messages:
        return False

    # Sort chronologically
    messages.sort(key=lambda m: m.get("createdDateTime", ""))

    last_msg_time = messages[-1].get("createdDateTime", "") if messages else ""

    fm = build_teams_channel_frontmatter(
        TeamsChannelData(
            team_name=team_name,
            channel_name=channel_name,
            channel_id=channel_id,
            last_message_time=last_msg_time,
        )
    )

    body_parts = [f"# {team_name} / {channel_name}\n"]

    body_parts.append("## Observations\n")
    body_parts.append(f"- [team] {team_name}")
    body_parts.append(f"- [channel] {channel_name}")
    body_parts.append(f"- [last_message_time] {last_msg_time}")
    body_parts.append(f"- [message_count] {len(messages)}")

    body_parts.append("\n---\n")
    body_parts.append("## Messages\n")

    for msg in messages:
        sender = extract_sender(msg)
        created = msg.get("createdDateTime", "")
        content = extract_content(msg, {})
        msg_type = msg.get("messageType", "")

        if msg_type == "systemEventMessage":
            continue

        timestamp_short = created[:16].replace("T", " ") if created else ""
        header = f"### {timestamp_short} -- {sender}\n" if sender else f"### {timestamp_short}\n"
        body_parts.append(header)
        if content:
            body_parts.append(content)
        body_parts.append("")

    content_str = dumps_markdown(fm, "\n".join(body_parts))

    team_slug = slugify(team_name, 80)
    channel_slug = slugify(channel_name, 80)
    hsh = short_hash(channel_id, 6)
    file_path = f"teams-channels/{team_slug}/{channel_slug}-{hsh}.md"

    storage.write_file(file_path, content_str)
    log.debug("teams_channels.wrote", team=team_name, channel=channel_name, messages=len(messages))
    return True
