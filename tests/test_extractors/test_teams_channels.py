"""Tests for Teams channel extractor."""

from __future__ import annotations

import re

import pytest
from pytest_httpx import HTTPXMock

from m365_extract.config import GraphConfig, TeamsChannelsExtractorConfig
from m365_extract.extractors import teams_channels
from m365_extract.graph_client import GraphClient
from m365_extract.storage.local import LocalBackend


@pytest.fixture()
def channels_config():
    return TeamsChannelsExtractorConfig(
        enabled=True,
        poll_interval_minutes=5,
    )


@pytest.fixture()
def graph_config():
    return GraphConfig(
        max_retries=1,
        backoff_base_ms=10,
        timeout_seconds=5,
        max_pages=10,
    )


class TestTeamsChannelsExtractor:
    def test_sync_produces_markdown(self, httpx_mock: HTTPXMock, tmp_path, graph_config, channels_config):
        httpx_mock.add_response(
            url=re.compile(r".*/me/joinedTeams.*"),
            json={"value": [{"id": "team-1", "displayName": "Engineering"}]},
        )
        httpx_mock.add_response(
            url=re.compile(r".*/teams/team-1/channels\?.*"),
            json={"value": [{"id": "ch-1", "displayName": "General"}]},
        )
        httpx_mock.add_response(
            url=re.compile(r".*/teams/team-1/channels/ch-1/messages/delta.*"),
            json={
                "value": [
                    {
                        "id": "msg-1",
                        "messageType": "message",
                        "createdDateTime": "2026-03-12T10:00:00Z",
                        "from": {"user": {"displayName": "Alice", "id": "u1"}},
                        "body": {"contentType": "text", "content": "Hello channel!"},
                    }
                ],
                "@odata.deltaLink": "https://delta?token=ch1",
            },
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        state, count = teams_channels.run(client, storage, {}, channels_config)

        assert count == 1
        files = storage.list_files("teams-channels")
        assert len(files) == 1

        content = storage.read_file(files[0])
        assert "Engineering" in content
        assert "General" in content
        assert "Hello channel!" in content
        client.close()

    def test_delta_link_persisted(self, httpx_mock: HTTPXMock, tmp_path, graph_config, channels_config):
        httpx_mock.add_response(
            url=re.compile(r".*/me/joinedTeams.*"),
            json={"value": [{"id": "team-1", "displayName": "Eng"}]},
        )
        httpx_mock.add_response(
            url=re.compile(r".*/teams/team-1/channels\?.*"),
            json={"value": [{"id": "ch-1", "displayName": "General"}]},
        )
        httpx_mock.add_response(
            url=re.compile(r".*/teams/team-1/channels/ch-1/messages/delta.*"),
            json={
                "value": [
                    {
                        "id": "msg-1",
                        "messageType": "message",
                        "createdDateTime": "2026-03-12T10:00:00Z",
                        "from": {"user": {"displayName": "A"}},
                        "body": {"contentType": "text", "content": "hi"},
                    }
                ],
                "@odata.deltaLink": "https://delta?token=persisted",
            },
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        state, count = teams_channels.run(client, storage, {}, channels_config)
        assert state["delta_team-1_ch-1"] == "https://delta?token=persisted"
        client.close()

    def test_empty_team_list(self, httpx_mock: HTTPXMock, tmp_path, graph_config, channels_config):
        httpx_mock.add_response(
            url=re.compile(r".*/me/joinedTeams.*"),
            json={"value": []},
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        state, count = teams_channels.run(client, storage, {}, channels_config)
        assert count == 0
        client.close()

    def test_channel_with_no_messages(self, httpx_mock: HTTPXMock, tmp_path, graph_config, channels_config):
        httpx_mock.add_response(
            url=re.compile(r".*/me/joinedTeams.*"),
            json={"value": [{"id": "team-1", "displayName": "Eng"}]},
        )
        httpx_mock.add_response(
            url=re.compile(r".*/teams/team-1/channels\?.*"),
            json={"value": [{"id": "ch-1", "displayName": "General"}]},
        )
        httpx_mock.add_response(
            url=re.compile(r".*/teams/team-1/channels/ch-1/messages/delta.*"),
            json={"value": [], "@odata.deltaLink": "https://delta?token=empty"},
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        state, count = teams_channels.run(client, storage, {}, channels_config)
        assert count == 0
        client.close()
