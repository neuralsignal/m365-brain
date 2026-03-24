"""Tests for email extractor."""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import patch

import pytest
from pytest_httpx import HTTPXMock

from m365_extract.config import EmailExtractorConfig, GraphConfig
from m365_extract.extractors import email
from m365_extract.graph_client import GraphClient
from m365_extract.storage.local import LocalBackend

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


@pytest.fixture()
def email_config():
    return EmailExtractorConfig(
        enabled=True,
        poll_interval_minutes=3,
        folders=["Inbox"],
        lookback_days=30,
        max_items_per_sync=100,
    )


@pytest.fixture()
def graph_config():
    return GraphConfig(
        max_retries=1,
        backoff_base_ms=10,
        timeout_seconds=5,
        max_pages=10,
    )


@pytest.fixture()
def email_response():
    return json.loads((FIXTURES_DIR / "email_response.json").read_text())


class TestEmailExtractor:
    def test_sync_produces_markdown(self, httpx_mock: HTTPXMock, tmp_path, graph_config, email_config, email_response):
        httpx_mock.add_response(
            url=re.compile(r".*/me/mailFolders/Inbox/messages/delta.*"),
            json=email_response,
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        state, count = email.run(client, storage, {}, email_config)

        assert count == 2
        assert "delta_link_Inbox" in state
        assert "last_sync" in state

        files = storage.list_files("emails")
        assert len(files) == 2

        client.close()

    def test_incremental_sync_uses_delta_link(self, httpx_mock: HTTPXMock, tmp_path, graph_config, email_config):
        delta_url = "https://graph.microsoft.com/v1.0/me/mailFolders/Inbox/messages/delta?$deltatoken=existing"
        httpx_mock.add_response(
            url=delta_url,
            json={
                "value": [
                    {
                        "id": "new-msg-1",
                        "subject": "New email",
                        "body": {"contentType": "text", "content": "Hello"},
                        "from": {"emailAddress": {"name": "Test", "address": "test@example.com"}},
                        "toRecipients": [],
                        "receivedDateTime": "2026-03-12T15:00:00Z",
                        "importance": "normal",
                        "hasAttachments": False,
                        "webLink": "",
                        "parentFolderId": "inbox",
                    }
                ],
                "@odata.deltaLink": "https://graph.microsoft.com/v1.0/delta?token=new",
            },
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        existing_state = {"delta_link_Inbox": delta_url}
        state, count = email.run(client, storage, existing_state, email_config)

        assert count == 1
        assert state["delta_link_Inbox"] == "https://graph.microsoft.com/v1.0/delta?token=new"
        client.close()

    def test_empty_response(self, httpx_mock: HTTPXMock, tmp_path, graph_config, email_config):
        httpx_mock.add_response(
            url=re.compile(r".*/me/mailFolders/Inbox/messages/delta.*"),
            json={"value": [], "@odata.deltaLink": "https://delta?token=empty"},
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        state, count = email.run(client, storage, {}, email_config)
        assert count == 0
        client.close()

    def test_html_body_converted_to_markdown(
        self, httpx_mock: HTTPXMock, tmp_path, graph_config, email_config, email_response
    ):
        httpx_mock.add_response(
            url=re.compile(r".*/me/mailFolders/Inbox/messages/delta.*"),
            json=email_response,
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        email.run(client, storage, {}, email_config)

        files = storage.list_files("emails")
        all_content = "\n".join(storage.read_file(f) for f in files)

        # HTML should be converted — no raw <html> tags
        assert "<html>" not in all_content
        # Content from the HTML email should be present as markdown
        assert "budget" in all_content.lower()
        client.close()

    def test_multiple_folders(self, httpx_mock: HTTPXMock, tmp_path, graph_config):
        config = EmailExtractorConfig(
            enabled=True,
            poll_interval_minutes=3,
            folders=["Inbox", "SentItems"],
            lookback_days=30,
            max_items_per_sync=100,
        )

        for folder in ["Inbox", "SentItems"]:
            httpx_mock.add_response(
                url=re.compile(rf".*/me/mailFolders/{folder}/messages/delta.*"),
                json={
                    "value": [
                        {
                            "id": f"msg-{folder}",
                            "subject": f"Email in {folder}",
                            "body": {"contentType": "text", "content": "Body"},
                            "from": {"emailAddress": {"name": "Test", "address": "test@example.com"}},
                            "toRecipients": [],
                            "receivedDateTime": "2026-03-12T10:00:00Z",
                            "importance": "normal",
                            "hasAttachments": False,
                            "webLink": "",
                            "parentFolderId": folder,
                        }
                    ],
                    "@odata.deltaLink": f"https://delta?token={folder}",
                },
            )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        state, count = email.run(client, storage, {}, config)
        assert count == 2
        assert "delta_link_Inbox" in state
        assert "delta_link_SentItems" in state
        client.close()

    def test_initial_sync_logs_sync_type(self, httpx_mock: HTTPXMock, tmp_path, graph_config, email_config):
        """Initial sync (no delta_link) logs sync_type='initial'."""
        httpx_mock.add_response(
            url=re.compile(r".*/me/mailFolders/Inbox/messages/delta.*"),
            json={"value": [], "@odata.deltaLink": "https://delta?token=init"},
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        events: list[dict] = []

        def capture_log(event, **kwargs):
            events.append({"event": event, **kwargs})

        with patch.object(email.log, "info", side_effect=capture_log):
            email.run(client, storage, {}, email_config)

        sync_start_events = [e for e in events if e["event"] == "email.folder_sync_start"]
        assert len(sync_start_events) == 1
        assert sync_start_events[0]["sync_type"] == "initial"
        client.close()

    def test_incremental_sync_logs_sync_type(self, httpx_mock: HTTPXMock, tmp_path, graph_config, email_config):
        """Incremental sync (with delta_link) logs sync_type='incremental'."""
        delta_url = "https://graph.microsoft.com/v1.0/me/mailFolders/Inbox/messages/delta?$deltatoken=existing"
        httpx_mock.add_response(
            url=delta_url,
            json={"value": [], "@odata.deltaLink": "https://delta?token=new"},
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        events: list[dict] = []

        def capture_log(event, **kwargs):
            events.append({"event": event, **kwargs})

        with patch.object(email.log, "info", side_effect=capture_log):
            email.run(client, storage, {"delta_link_Inbox": delta_url}, email_config)

        sync_start_events = [e for e in events if e["event"] == "email.folder_sync_start"]
        assert len(sync_start_events) == 1
        assert sync_start_events[0]["sync_type"] == "incremental"
        client.close()

    def test_skips_invalid_messages(self, httpx_mock: HTTPXMock, tmp_path, graph_config, email_config):
        httpx_mock.add_response(
            url=re.compile(r".*/me/mailFolders/Inbox/messages/delta.*"),
            json={
                "value": [
                    {"id": "", "subject": "No ID", "receivedDateTime": "2026-03-12T10:00:00Z"},
                    {"id": "valid-id", "subject": "No Date"},
                ],
                "@odata.deltaLink": "https://delta?token=skip",
            },
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        state, count = email.run(client, storage, {}, email_config)
        assert count == 0
        client.close()
