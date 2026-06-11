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
        download_attachments=False,
        download_inline_images=False,
        max_attachment_size_mb=25,
        attachment_convert_extensions=[],
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

        state, count = teams_chats.run(client, storage, {}, teams_config, {})

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

        teams_chats.run(client, storage, {}, teams_config, {})

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
        state, count = teams_chats.run(client, storage, existing_state, teams_config, {})

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

        state, count = teams_chats.run(client, storage, {}, teams_config, {})
        assert count == 0
        client.close()

    def test_truncation_indicator_when_limit_reached(self, httpx_mock: HTTPXMock, tmp_path, graph_config):
        """When messages count equals max_messages_per_chat, frontmatter has message_limit_reached."""
        config = TeamsChatsExtractorConfig(
            enabled=True,
            poll_interval_minutes=5,
            max_messages_per_chat=2,
            download_attachments=False,
            download_inline_images=False,
            max_attachment_size_mb=25,
            attachment_convert_extensions=[],
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

        teams_chats.run(client, storage, {}, config, {})

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

        teams_chats.run(client, storage, {}, teams_config, {})

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

        teams_chats.run(client, storage, {}, teams_config, {})

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

        teams_chats.run(client, storage, {}, teams_config, {})

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

        state, count = teams_chats.run(client, storage, {}, teams_config, {})
        assert count == 0
        assert storage.list_files("teams-chats") == []
        client.close()


class TestFolderLayout:
    """Tests covering the per-chat folder layout and attachment link rendering."""

    def test_message_md_written_under_chat_folder(self, httpx_mock: HTTPXMock, tmp_path, graph_config, teams_config):
        """Each chat lives in its own directory with a messages.md inside."""
        httpx_mock.add_response(
            url=re.compile(r".*/me/chats\?.*"),
            json={
                "value": [
                    {
                        "id": "chat-folder",
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
                        "createdDateTime": "2026-03-12T10:00:00Z",
                        "from": {"user": {"displayName": "Alice", "id": "u1"}},
                        "body": {"contentType": "text", "content": "Hi"},
                    },
                ]
            },
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        teams_chats.run(client, storage, {}, teams_config, {})

        files = storage.list_files("teams-chats")
        assert any(f.endswith("/messages.md") for f in files)
        assert all("/messages.md" in f for f in files if f.endswith(".md"))
        client.close()

    def test_attachment_inline_link_rendered_in_body(self, httpx_mock: HTTPXMock, tmp_path, graph_config):
        """When an attachment is downloaded, messages.md contains an inline link."""
        config = TeamsChatsExtractorConfig(
            enabled=True,
            poll_interval_minutes=5,
            max_messages_per_chat=200,
            download_attachments=True,
            download_inline_images=False,
            max_attachment_size_mb=100,
            attachment_convert_extensions=[],
        )
        content_url = "https://sanoptis.sharepoint.com/sites/x/spec.pdf"
        import base64 as _b64

        encoded = "u!" + _b64.urlsafe_b64encode(content_url.encode()).decode().rstrip("=")

        httpx_mock.add_response(
            url=re.compile(r".*/me/chats\?.*"),
            json={
                "value": [
                    {
                        "id": "chat-att",
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
                        "id": "msg-att",
                        "messageType": "message",
                        "createdDateTime": "2026-03-12T10:00:00Z",
                        "from": {"user": {"displayName": "Alice", "id": "u1"}},
                        "body": {"contentType": "text", "content": "see attached"},
                        "attachments": [{"contentType": "reference", "name": "spec.pdf", "contentUrl": content_url}],
                    }
                ]
            },
        )
        httpx_mock.add_response(
            url=re.compile(rf".*/shares/{re.escape(encoded)}/driveItem.*"),
            json={
                "id": "drive-item",
                "size": 64,
                "@microsoft.graph.downloadUrl": "https://sanoptis.sharepoint.com/dl?t=x",
            },
        )
        httpx_mock.add_response(
            url=re.compile(r"https://sanoptis\.sharepoint\.com/dl.*"),
            content=b"%PDF-1.4 fake",
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        teams_chats.run(client, storage, {}, config, {})

        files = storage.list_files("teams-chats")
        messages_md = [f for f in files if f.endswith("/messages.md")][0]
        content = storage.read_file(messages_md)
        assert "**Attachments:**" in content
        assert "[spec.pdf](attachments/msg-att/spec.pdf)" in content
        assert any(f.endswith("attachments/msg-att/spec.pdf") for f in files)
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

    def test_returns_false_on_graph_api_error(self, chat, teams_config):
        """When get_paginated raises GraphApiError, _process_chat returns False."""
        mock_client = MagicMock(spec=GraphClient)
        mock_client.get_paginated.side_effect = GraphApiError("403 Forbidden", 403)
        mock_storage = MagicMock()

        result = teams_chats._process_chat(mock_client, mock_storage, chat, None, 200, teams_config, {}, {})

        assert result is False
        mock_storage.write_file.assert_not_called()

    def test_skips_write_when_last_message_unchanged(self, chat, teams_config):
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

        result = teams_chats._process_chat(mock_client, mock_storage, chat, None, 200, teams_config, {}, {})

        assert result is False
        mock_storage.write_file.assert_not_called()

    def test_continues_write_on_existing_file_parse_failure(self, chat, teams_config):
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

        result = teams_chats._process_chat(mock_client, mock_storage, chat, None, 200, teams_config, {}, {})

        assert result is True
        mock_storage.write_file.assert_called_once()


class TestExtractChatData:
    """Tests for _extract_chat_data pure extraction function."""

    def test_extracts_full_chat(self):
        chat = {
            "id": "chat-1",
            "chatType": "group",
            "topic": "Project Alpha",
            "members": [{"displayName": "Alice"}, {"displayName": "Bob"}],
        }
        messages = [
            {"id": "m2", "createdDateTime": "2026-03-12T10:00:00Z", "messageType": "message"},
            {"id": "m1", "createdDateTime": "2026-03-12T09:00:00Z", "messageType": "message"},
        ]

        data, sorted_messages, file_path = teams_chats._extract_chat_data(chat, messages, 200)

        assert data.conversation_id == "chat-1"
        assert data.conversation_type == "group"
        assert data.title == "Project Alpha"
        assert data.participants == ["Alice", "Bob"]
        assert sorted_messages[0]["id"] == "m1"
        assert sorted_messages[1]["id"] == "m2"
        assert data.last_message_time == "2026-03-12T10:00:00Z"
        assert data.message_limit_reached is False
        assert "teams-chats/" in file_path

    def test_title_from_participants_when_no_topic(self):
        chat = {
            "id": "chat-2",
            "chatType": "oneOnOne",
            "topic": None,
            "members": [{"displayName": "Charlie"}, {"displayName": "Alice"}],
        }
        messages = [{"id": "m1", "createdDateTime": "2026-03-12T09:00:00Z"}]

        data, _messages, _file_path = teams_chats._extract_chat_data(chat, messages, 200)

        assert data.title == "Alice, Charlie"

    def test_title_defaults_to_chat_when_no_participants(self):
        chat = {"id": "chat-3", "chatType": "oneOnOne", "topic": None, "members": []}
        messages = [{"id": "m1", "createdDateTime": "2026-03-12T09:00:00Z"}]

        data, _messages, _file_path = teams_chats._extract_chat_data(chat, messages, 200)

        assert data.title == "Chat"

    def test_message_limit_reached_detected(self):
        chat = {"id": "chat-4", "chatType": "oneOnOne", "topic": None, "members": [{"displayName": "Alice"}]}
        messages = [
            {"id": "m1", "createdDateTime": "2026-03-12T09:00:00Z"},
            {"id": "m2", "createdDateTime": "2026-03-12T10:00:00Z"},
        ]

        data, _messages, _file_path = teams_chats._extract_chat_data(chat, messages, 2)

        assert data.message_limit_reached is True

    def test_skips_members_without_display_name(self):
        chat = {
            "id": "chat-5",
            "chatType": "oneOnOne",
            "topic": None,
            "members": [{"displayName": "Alice"}, {"displayName": ""}, {}],
        }
        messages = [{"id": "m1", "createdDateTime": "2026-03-12T09:00:00Z"}]

        data, _messages, _file_path = teams_chats._extract_chat_data(chat, messages, 200)

        assert data.participants == ["Alice"]
