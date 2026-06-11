"""Channel target resolution for the teams_channels extractor.

Discovery mode walks ``/me/joinedTeams`` and ``/teams/{id}/channels``, which
requires the ``Team.ReadBasic.All`` + ``Channel.ReadBasic.All`` delegated
scopes. Explicit mode builds targets purely from configured
:class:`~m365_extract.config.ExplicitChannel` entries — no Graph calls at
all — so reading a known channel works with ``ChannelMessage.Read.All`` alone.

Both functions return the same shape: ``(team_id, team_name, channel_dict)``
tuples, where ``channel_dict`` carries the ``id`` / ``displayName`` keys the
per-channel processing expects, making the two modes indistinguishable
downstream (watermarks, store, renderer, attachments, output folders).
"""

from __future__ import annotations

import structlog

from m365_extract.config import ExplicitChannel
from m365_extract.extractors._teams_ingest import GRAPH_PAGE_SIZE
from m365_extract.graph_client import GraphClient

log = structlog.get_logger()

ChannelTarget = tuple[str, str, dict]


def discover_targets(client: GraphClient) -> list[ChannelTarget]:
    """List every channel of every joined team via Graph discovery."""
    teams = list(client.get_paginated("/me/joinedTeams", params=None, max_pages=client.max_pages))
    log.info("teams_channels.fetched_teams", count=len(teams))
    targets: list[ChannelTarget] = []
    for team in teams:
        team_id = team.get("id", "")
        team_name = team.get("displayName", "Unknown Team")
        channels = client.get_paginated(
            f"/teams/{team_id}/channels",
            params={"$top": str(GRAPH_PAGE_SIZE)},
            max_pages=client.max_pages,
        )
        targets.extend((team_id, team_name, channel) for channel in channels)
    return targets


def explicit_targets(channels: list[ExplicitChannel]) -> list[ChannelTarget]:
    """Build targets from the configured channel list — no Graph calls."""
    log.info("teams_channels.explicit_channels", count=len(channels))
    return [
        (entry.team_id, entry.team_name, {"id": entry.channel_id, "displayName": entry.channel_name})
        for entry in channels
    ]
