"""Tests for Teams chat extractor."""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pytest_httpx import HTTPXMock

from m365_extract.config import GraphConfig, TeamsChatsExtractorConfig
from m365_extract.extractors import teams_chats
from m365_extract.graph_client import GraphApiError, GraphClient
from m365_extract.markdown_writer import dumps_markdown
from m365_extract.storage.local import LocalBackend

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


@pytest.fixture()
def teams_config():
    return TeamsChatsExtractorConfig(
        enabled=True,
        poll_interval_minutes=5,
        max_messages_per_chat=200,
    )


@pytest.fixture()
def graph_config():
    return GraphConfig(
        max_retries=1,
        backoff_base_ms=10,
        timeout_seconds=5,
        max_pages=10,
        max_retry_after_seconds=300.0,
        error_message_max_length=200,
    )


@pytest.fixture()
def chats_response():
    return json.loads((FIXTURES_DIR / "teams_chat_response.json").read_text())


@pytest.fixture()
def messages_response():
    return json.loads((FIXTURES_DIR / "teams_messages_response.json").read_text())


class TestTeamsChatsExtractor:
    def test_sync_produces_markdown(
        self, httpx_mock: HTTPXMock, tmp_path, graph_config, teams_config, chats_response, messages_response
    ):
        httpx_mock.add_response(
            url=re.compile(r".*/me/chats\?.*"),
            json=chats_response,
        )
        for _ in range(2):
            httpx_mock.add_response(
                url=re.compile(r".*/me/chats/.*/messages.*"),
                json=messages_response,
            )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        state, count = teams_chats.run(client, storage, {}, teams_config)

        assert count == 2
        assert "last_sync" in state
        assert state["chats_synced"] == 2

        files = storage.list_files("teams-chats")
        assert len(files) == 2
        client.close()

    def test_system_messages_skipped(self, httpx_mock: HTTPXMock, tmp_path, graph_config, teams_config):
        httpx_mock.add_response(
            url=re.compile(r".*/me/chats\?.*"),
            json={
                "value": [
                    {
                        "id": "chat-1",
                        "chatType": "oneOnOne",
                        "topic": None,
                        "members": [{"displayName": "Alice"}, {"displayName": "Bob"}],
                    }
                ]
            },
        )
        httpx_mock.add_response(
            url=re.compile(r".*/me/chats/.*/messages.*"),
            json={
                "value": [
                    {
                        "id": "sys-msg",
                        "messageType": "systemEventMessage",
                        "createdDateTime": "2026-03-12T09:00:00Z",
                        "from": None,
                        "body": {"contentType": "html", "content": "system event"},
                    },
                    {
                        "id": "real-msg",
                        "messageType": "message",
                        "createdDateTime": "2026-03-12T10:00:00Z",
                        "from": {"user": {"displayName": "Alice", "id": "u1"}},
                        "body": {"contentType": "text", "content": "Hello!"},
                    },
                ]
            },
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        teams_chats.run(client, storage, {}, teams_config)

        files = storage.list_files("teams-chats")
        content = storage.read_file(files[0])
        assert "Hello!" in content
        assert "system event" not in content
        client.close()

    def test_incremental_with_filter(self, httpx_mock: HTTPXMock, tmp_path, graph_config, teams_config):
        httpx_mock.add_response(
            url=re.compile(r".*/me/chats\?.*"),
            json={
                "value": [
                    {
                        "id": "chat-1",
                        "chatType": "oneOnOne",
                        "topic": None,
                        "members": [{"displayName": "Alice"}, {"displayName": "Bob"}],
                    }
                ]
            },
        )
        httpx_mock.add_response(
            url=re.compile(r".*/me/chats/.*/messages.*"),
            json={
                "value": [
                    {
                        "id": "new-msg",
                        "messageType": "message",
                        "createdDateTime": "2026-03-12T12:00:00Z",
                        "from": {"user": {"displayName": "Alice", "id": "u1"}},
                        "body": {"contentType": "text", "content": "New message"},
                    }
                ]
            },
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        existing_state = {"last_sync": "2026-03-12T10:00:00Z"}
        state, count = teams_chats.run(client, storage, existing_state, teams_config)

        assert count == 1
        requests = httpx_mock.get_requests()
        msg_request = [r for r in requests if "/messages" in str(r.url)][0]
        assert "lastModifiedDateTime" in str(msg_request.url)
        client.close()

    def test_empty_chats(self, httpx_mock: HTTPXMock, tmp_path, graph_config, teams_config):
        httpx_mock.add_response(
            url=re.compile(r".*/me/chats\?.*"),
            json={"value": []},
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        state, count = teams_chats.run(client, storage, {}, teams_config)
        assert count == 0
        client.close()

    def test_truncation_indicator_when_limit_reached(self, httpx_mock: HTTPXMock, tmp_path, graph_config):
        """When messages count equals max_messages_per_chat, frontmatter has message_limit_reached."""
        config = TeamsChatsExtractorConfig(
            enabled=True,
            poll_interval_minutes=5,
            max_messages_per_chat=2,
        )
        httpx_mock.add_response(
            url=re.compile(r".*/me/chats\?.*"),
            json={
                "value": [
                    {
                        "id": "chat-trunc",
                        "chatType": "oneOnOne",
                        "topic": None,
                        "members": [{"displayName": "Alice"}, {"displayName": "Bob"}],
                    }
                ]
            },
        )
        httpx_mock.add_response(
            url=re.compile(r".*/me/chats/.*/messages.*"),
            json={
                "value": [
                    {
                        "id": "msg-1",
                        "messageType": "message",
                        "createdDateTime": "2026-03-12T09:00:00Z",
                        "from": {"user": {"displayName": "Alice", "id": "u1"}},
                        "body": {"contentType": "text", "content": "Hello"},
                    },
                    {
                        "id": "msg-2",
                        "messageType": "message",
                        "createdDateTime": "2026-03-12T10:00:00Z",
                        "from": {"user": {"displayName": "Bob", "id": "u2"}},
                        "body": {"contentType": "text", "content": "Hi"},
                    },
                ]
            },
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        teams_chats.run(client, storage, {}, config)

        files = storage.list_files("teams-chats")
        assert len(files) == 1
        content = storage.read_file(files[0])
        assert "message_limit_reached: true" in content
        client.close()

    def test_no_truncation_when_below_limit(self, httpx_mock: HTTPXMock, tmp_path, graph_config, teams_config):
        """When messages are below max_messages_per_chat, flag is absent from frontmatter."""
        httpx_mock.add_response(
            url=re.compile(r".*/me/chats\?.*"),
            json={
                "value": [
                    {
                        "id": "chat-small",
                        "chatType": "oneOnOne",
                        "topic": None,
                        "members": [{"displayName": "Alice"}, {"displayName": "Bob"}],
                    }
                ]
            },
        )
        httpx_mock.add_response(
            url=re.compile(r".*/me/chats/.*/messages.*"),
            json={
                "value": [
                    {
                        "id": "msg-1",
                        "messageType": "message",
                        "createdDateTime": "2026-03-12T09:00:00Z",
                        "from": {"user": {"displayName": "Alice", "id": "u1"}},
                        "body": {"contentType": "text", "content": "Short chat"},
                    },
                ]
            },
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        teams_chats.run(client, storage, {}, teams_config)

        files = storage.list_files("teams-chats")
        assert len(files) == 1
        content = storage.read_file(files[0])
        assert "message_limit_reached" not in content
        client.close()

    def test_group_chat_type(self, httpx_mock: HTTPXMock, tmp_path, graph_config, teams_config):
        """Group chat with 4+ members produces correct tags and participant list."""
        httpx_mock.add_response(
            url=re.compile(r".*/me/chats\?.*"),
            json={
                "value": [
                    {
                        "id": "chat-group-4",
                        "chatType": "group",
                        "topic": None,
                        "members": [
                            {"displayName": "Alice"},
                            {"displayName": "Bob"},
                            {"displayName": "Carol"},
                            {"displayName": "Dave"},
                        ],
                    }
                ]
            },
        )
        httpx_mock.add_response(
            url=re.compile(r".*/me/chats/.*/messages.*"),
            json={
                "value": [
                    {
                        "id": "msg-grp",
                        "messageType": "message",
                        "createdDateTime": "2026-03-12T10:00:00Z",
                        "from": {"user": {"displayName": "Alice", "id": "u1"}},
                        "body": {"contentType": "text", "content": "Group discussion"},
                    },
                ]
            },
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        teams_chats.run(client, storage, {}, teams_config)

        files = storage.list_files("teams-chats")
        assert len(files) == 1
        content = storage.read_file(files[0])
        assert "teams-group" in content
        assert "Alice" in content
        assert "Dave" in content
        assert "Group discussion" in content
        client.close()

    def test_chat_with_topic(self, httpx_mock: HTTPXMock, tmp_path, graph_config, teams_config):
        """Group chat with topic set uses topic as title instead of participant names."""
        httpx_mock.add_response(
            url=re.compile(r".*/me/chats\?.*"),
            json={
                "value": [
                    {
                        "id": "chat-topic",
                        "chatType": "group",
                        "topic": "Project Alpha Planning",
                        "members": [
                            {"displayName": "Alice"},
                            {"displayName": "Bob"},
                        ],
                    }
                ]
            },
        )
        httpx_mock.add_response(
            url=re.compile(r".*/me/chats/.*/messages.*"),
            json={
                "value": [
                    {
                        "id": "msg-topic",
                        "messageType": "message",
                        "createdDateTime": "2026-03-12T11:00:00Z",
                        "from": {"user": {"displayName": "Alice", "id": "u1"}},
                        "body": {"contentType": "text", "content": "Let's plan"},
                    },
                ]
            },
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        teams_chats.run(client, storage, {}, teams_config)

        files = storage.list_files("teams-chats")
        assert len(files) == 1
        content = storage.read_file(files[0])
        # Title should be the topic, not "Alice, Bob"
        assert "# Project Alpha Planning" in content
        assert "Alice, Bob" not in content.split("---")[0]  # Not in title area
        client.close()

    def test_chat_with_no_messages_not_written(self, httpx_mock: HTTPXMock, tmp_path, graph_config, teams_config):
        httpx_mock.add_response(
            url=re.compile(r".*/me/chats\?.*"),
            json={
                "value": [
                    {
                        "id": "chat-empty",
                        "chatType": "oneOnOne",
                        "topic": None,
                        "members": [{"displayName": "Alice"}],
                    }
                ]
            },
        )
        httpx_mock.add_response(
            url=re.compile(r".*/me/chats/.*/messages.*"),
            json={"value": []},
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        state, count = teams_chats.run(client, storage, {}, teams_config)
        assert count == 0
        assert storage.list_files("teams-chats") == []
        client.close()


class TestProcessChat:
    """Tests for _process_chat covering missed coverage paths."""

    @pytest.fixture()
    def chat(self):
        return {
            "id": "chat-1",
            "chatType": "oneOnOne",
            "topic": None,
            "members": [{"displayName": "Alice"}, {"displayName": "Bob"}],
        }

    def test_returns_false_on_graph_api_error(self, chat):
        """When get_paginated raises GraphApiError, _process_chat returns False."""
        mock_client = MagicMock(spec=GraphClient)
        mock_client.get_paginated.side_effect = GraphApiError("403 Forbidden")
        mock_storage = MagicMock()

        result = teams_chats._process_chat(mock_client, mock_storage, chat, None, 200)

        assert result is False
        mock_storage.write_file.assert_not_called()

    def test_skips_write_when_last_message_unchanged(self, chat):
        """When existing file has same last_message_time, returns False without writing."""
        last_msg_time = "2026-03-12T10:00:00Z"
        mock_client = MagicMock(spec=GraphClient)
        mock_client.get_paginated.return_value = iter(
            [
                {
                    "id": "msg-1",
                    "messageType": "message",
                    "createdDateTime": last_msg_time,
                    "from": {"user": {"displayName": "Alice", "id": "u1"}},
                    "body": {"contentType": "text", "content": "Hello"},
                },
            ]
        )

        existing_fm = {"last_message_time": last_msg_time}
        existing_content = dumps_markdown(existing_fm, "# old body")

        mock_storage = MagicMock()
        mock_storage.file_exists.return_value = True
        mock_storage.read_file.return_value = existing_content

        result = teams_chats._process_chat(mock_client, mock_storage, chat, None, 200)

        assert result is False
        mock_storage.write_file.assert_not_called()

    def test_continues_write_on_existing_file_parse_failure(self, chat):
        """When loads_markdown raises ValueError, logs warning and writes the file."""
        mock_client = MagicMock(spec=GraphClient)
        mock_client.get_paginated.return_value = iter(
            [
                {
                    "id": "msg-1",
                    "messageType": "message",
                    "createdDateTime": "2026-03-12T10:00:00Z",
                    "from": {"user": {"displayName": "Alice", "id": "u1"}},
                    "body": {"contentType": "text", "content": "Hello"},
                },
            ]
        )

        mock_storage = MagicMock()
        mock_storage.file_exists.return_value = True
        mock_storage.read_file.side_effect = ValueError("invalid frontmatter")

        result = teams_chats._process_chat(mock_client, mock_storage, chat, None, 200)

        assert result is True
        mock_storage.write_file.assert_called_once()
