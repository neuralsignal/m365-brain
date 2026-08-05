"""Tests for channel target resolution (discovery vs explicit mode)."""

from __future__ import annotations

from unittest.mock import MagicMock

from m365_brain.config import ExplicitChannel
from m365_brain.m365.client import GraphClient
from m365_brain.m365.extractors._teams_channel_targets import discover_targets, explicit_targets


def _channel(team_id: str, channel_id: str) -> ExplicitChannel:
    return ExplicitChannel(
        team_id=team_id,
        channel_id=channel_id,
        team_name=f"Team {team_id}",
        channel_name=f"Channel {channel_id}",
    )


class TestExplicitTargets:
    def test_builds_the_discovery_shape_from_config(self) -> None:
        targets = explicit_targets([_channel("t1", "c1"), _channel("t2", "c2")])
        assert targets == [
            ("t1", "Team t1", {"id": "c1", "displayName": "Channel c1"}),
            ("t2", "Team t2", {"id": "c2", "displayName": "Channel c2"}),
        ]

    def test_empty_config_yields_no_targets(self) -> None:
        assert explicit_targets([]) == []


class TestDiscoverTargets:
    def test_flattens_every_channel_of_every_joined_team(self) -> None:
        client = MagicMock(spec=GraphClient)
        client.max_pages = 7
        client.get_paginated.side_effect = [
            iter([{"id": "t1", "displayName": "Alpha"}, {"id": "t2", "displayName": "Beta"}]),
            iter([{"id": "c1", "displayName": "General"}, {"id": "c2", "displayName": "Random"}]),
            iter([{"id": "c3", "displayName": "General"}]),
        ]

        targets = discover_targets(client)

        assert targets == [
            ("t1", "Alpha", {"id": "c1", "displayName": "General"}),
            ("t1", "Alpha", {"id": "c2", "displayName": "Random"}),
            ("t2", "Beta", {"id": "c3", "displayName": "General"}),
        ]
        paths = [call.args[0] for call in client.get_paginated.call_args_list]
        assert paths == ["/me/joinedTeams", "/teams/t1/channels", "/teams/t2/channels"]
        assert all(call.kwargs["max_pages"] == 7 for call in client.get_paginated.call_args_list)

    def test_team_without_display_name_falls_back(self) -> None:
        client = MagicMock(spec=GraphClient)
        client.max_pages = 3
        client.get_paginated.side_effect = [
            iter([{"id": "t9"}]),
            iter([{"id": "c9", "displayName": "General"}]),
        ]
        assert discover_targets(client) == [("t9", "Unknown Team", {"id": "c9", "displayName": "General"})]

    def test_team_with_no_channels_contributes_nothing(self) -> None:
        client = MagicMock(spec=GraphClient)
        client.max_pages = 3
        client.get_paginated.side_effect = [iter([{"id": "t1", "displayName": "Alpha"}]), iter([])]
        assert discover_targets(client) == []
