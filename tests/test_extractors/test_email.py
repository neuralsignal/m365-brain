"""Tests for email extractor."""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from pytest_httpx import HTTPXMock

from m365_extract.config import EmailExtractorConfig, GraphConfig
from m365_extract.extractors import email
from m365_extract.graph_client import GraphClient
from m365_extract.storage.local import LocalBackend

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"

# Empty converters config used by tests that don't exercise conversion
_NO_CONVERTERS: dict = {}


@pytest.fixture()
def email_config():
    return EmailExtractorConfig(
        enabled=True,
        poll_interval_minutes=3,
        folders=["Inbox"],
        lookback_days=30,
        max_items_per_sync=100,
        download_attachments=False,
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

        state, count = email.run(client, storage, {}, email_config, _NO_CONVERTERS)

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
        state, count = email.run(client, storage, existing_state, email_config, _NO_CONVERTERS)

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

        state, count = email.run(client, storage, {}, email_config, _NO_CONVERTERS)
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

        email.run(client, storage, {}, email_config, _NO_CONVERTERS)

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
            download_attachments=False,
            max_attachment_size_mb=25,
            attachment_convert_extensions=[],
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

        state, count = email.run(client, storage, {}, config, _NO_CONVERTERS)
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
            email.run(client, storage, {}, email_config, _NO_CONVERTERS)

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
            email.run(client, storage, {"delta_link_Inbox": delta_url}, email_config, _NO_CONVERTERS)

        sync_start_events = [e for e in events if e["event"] == "email.folder_sync_start"]
        assert len(sync_start_events) == 1
        assert sync_start_events[0]["sync_type"] == "incremental"
        client.close()

    def test_very_long_subject(self, httpx_mock: HTTPXMock, tmp_path, graph_config, email_config):
        """500-char subject should be slugified and truncated to a valid file path."""
        long_subject = "A" * 500
        httpx_mock.add_response(
            url=re.compile(r".*/me/mailFolders/Inbox/messages/delta.*"),
            json={
                "value": [
                    {
                        "id": "msg-long-subj",
                        "subject": long_subject,
                        "body": {"contentType": "text", "content": "Body"},
                        "from": {"emailAddress": {"name": "Test", "address": "test@example.com"}},
                        "toRecipients": [],
                        "receivedDateTime": "2026-03-12T10:00:00Z",
                        "importance": "normal",
                        "hasAttachments": False,
                        "webLink": "",
                        "parentFolderId": "inbox",
                    }
                ],
                "@odata.deltaLink": "https://delta?token=long",
            },
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        state, count = email.run(client, storage, {}, email_config, _NO_CONVERTERS)
        assert count == 1

        files = storage.list_files("emails")
        assert len(files) == 1
        # File path slug should be truncated (max_length=80 default for slugify)
        assert len(files[0]) < 200
        client.close()

    def test_subject_with_yaml_special_chars(self, httpx_mock: HTTPXMock, tmp_path, graph_config, email_config):
        """Subject with YAML special characters must not corrupt frontmatter."""
        tricky_subject = 'RE: FW: Budget (Q1) — Final #2 [v3]: "approved"'
        httpx_mock.add_response(
            url=re.compile(r".*/me/mailFolders/Inbox/messages/delta.*"),
            json={
                "value": [
                    {
                        "id": "msg-yaml-chars",
                        "subject": tricky_subject,
                        "body": {"contentType": "text", "content": "Approved."},
                        "from": {"emailAddress": {"name": "Boss", "address": "boss@example.com"}},
                        "toRecipients": [{"emailAddress": {"address": "me@example.com"}}],
                        "receivedDateTime": "2026-03-12T14:00:00Z",
                        "importance": "high",
                        "hasAttachments": False,
                        "webLink": "",
                        "parentFolderId": "inbox",
                    }
                ],
                "@odata.deltaLink": "https://delta?token=yaml",
            },
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        email.run(client, storage, {}, email_config, _NO_CONVERTERS)

        files = storage.list_files("emails")
        content = storage.read_file(files[0])

        from m365_extract.markdown_writer import loads_markdown

        fm, body = loads_markdown(content)
        assert fm["title"] == tricky_subject
        client.close()

    def test_missing_sender_fields(self, httpx_mock: HTTPXMock, tmp_path, graph_config, email_config):
        """Email with null from field should still be written with empty sender."""
        httpx_mock.add_response(
            url=re.compile(r".*/me/mailFolders/Inbox/messages/delta.*"),
            json={
                "value": [
                    {
                        "id": "msg-no-sender",
                        "subject": "System notification",
                        "body": {"contentType": "text", "content": "Alert"},
                        "from": None,
                        "toRecipients": [],
                        "receivedDateTime": "2026-03-12T08:00:00Z",
                        "importance": "normal",
                        "hasAttachments": False,
                        "webLink": "",
                        "parentFolderId": "inbox",
                    }
                ],
                "@odata.deltaLink": "https://delta?token=nosender",
            },
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        state, count = email.run(client, storage, {}, email_config, _NO_CONVERTERS)
        assert count == 1

        files = storage.list_files("emails")
        content = storage.read_file(files[0])
        assert "System notification" in content
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

        state, count = email.run(client, storage, {}, email_config, _NO_CONVERTERS)
        assert count == 0
        client.close()


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


class TestEmailDedup:
    """Emails with identical (received_minute, slug) are written only once per run."""

    def _make_msg(self, msg_id: str, subject: str, received: str) -> dict:
        return {
            "id": msg_id,
            "subject": subject,
            "body": {"contentType": "text", "content": "body"},
            "from": {"emailAddress": {"name": "Test", "address": "t@example.com"}},
            "toRecipients": [],
            "receivedDateTime": received,
            "importance": "normal",
            "hasAttachments": False,
            "webLink": "",
            "parentFolderId": "inbox",
        }

    def test_duplicate_within_run_is_skipped(
        self, httpx_mock: HTTPXMock, tmp_path, graph_config, email_config
    ):
        """Two messages with the same subject and same received-minute are deduplicated."""
        httpx_mock.add_response(
            url=re.compile(r".*/me/mailFolders/Inbox/messages/delta.*"),
            json={
                "value": [
                    self._make_msg("id-a1b2c3", "Meeting notes", "2026-03-12T10:30:00Z"),
                    self._make_msg("id-d4e5f6", "Meeting notes", "2026-03-12T10:30:45Z"),  # same minute
                ],
                "@odata.deltaLink": "https://delta?token=dedup",
            },
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        _, count = email.run(client, storage, {}, email_config, _NO_CONVERTERS)

        # Only the first one should be written
        assert count == 1
        files = storage.list_files("emails")
        assert len(files) == 1
        client.close()

    def test_different_minute_not_deduplicated(
        self, httpx_mock: HTTPXMock, tmp_path, graph_config, email_config
    ):
        """Same subject but different received-minute = two distinct emails."""
        httpx_mock.add_response(
            url=re.compile(r".*/me/mailFolders/Inbox/messages/delta.*"),
            json={
                "value": [
                    self._make_msg("id-aaa111", "Status update", "2026-03-12T10:00:00Z"),
                    self._make_msg("id-bbb222", "Status update", "2026-03-12T10:01:00Z"),
                ],
                "@odata.deltaLink": "https://delta?token=nodeduplicate",
            },
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        _, count = email.run(client, storage, {}, email_config, _NO_CONVERTERS)
        assert count == 2
        client.close()

    def test_different_subject_not_deduplicated(
        self, httpx_mock: HTTPXMock, tmp_path, graph_config, email_config
    ):
        """Different subjects at the same received-minute = two distinct emails."""
        httpx_mock.add_response(
            url=re.compile(r".*/me/mailFolders/Inbox/messages/delta.*"),
            json={
                "value": [
                    self._make_msg("id-ccc333", "Email A", "2026-03-12T10:00:00Z"),
                    self._make_msg("id-ddd444", "Email B", "2026-03-12T10:00:30Z"),
                ],
                "@odata.deltaLink": "https://delta?token=diffsubject",
            },
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        _, count = email.run(client, storage, {}, email_config, _NO_CONVERTERS)
        assert count == 2
        client.close()


# ---------------------------------------------------------------------------
# Attachment download
# ---------------------------------------------------------------------------


def _attachment_config() -> EmailExtractorConfig:
    return EmailExtractorConfig(
        enabled=True,
        poll_interval_minutes=3,
        folders=["Inbox"],
        lookback_days=30,
        max_items_per_sync=100,
        download_attachments=True,
        max_attachment_size_mb=25,
        attachment_convert_extensions=[],
    )


class TestEmailAttachments:
    def _make_email_msg(self, has_attachments: bool = True) -> dict:
        return {
            "id": "msg-with-attachment",
            "subject": "See attached",
            "body": {"contentType": "text", "content": "Please see attachment."},
            "from": {"emailAddress": {"name": "Sender", "address": "sender@example.com"}},
            "toRecipients": [],
            "receivedDateTime": "2026-03-12T10:00:00Z",
            "importance": "normal",
            "hasAttachments": has_attachments,
            "webLink": "",
            "parentFolderId": "inbox",
        }

    def test_attachment_binary_written(
        self, httpx_mock: HTTPXMock, tmp_path, graph_config
    ):
        """Attachment bytes are written to attachments/ subdir."""
        config = _attachment_config()
        httpx_mock.add_response(
            url=re.compile(r".*/me/mailFolders/Inbox/messages/delta.*"),
            json={
                "value": [self._make_email_msg()],
                "@odata.deltaLink": "https://delta?token=att",
            },
        )
        httpx_mock.add_response(
            url=re.compile(r".*/me/messages/msg-with-attachment/attachments.*"),
            json={
                "value": [
                    {
                        "id": "att-1",
                        "name": "report.pdf",
                        "contentType": "application/pdf",
                        "size": 1024,
                        "isInline": False,
                        "@microsoft.graph.downloadUrl": "https://attachments.office.com/report.pdf",
                    }
                ]
            },
        )
        httpx_mock.add_response(
            url="https://attachments.office.com/report.pdf",
            content=b"%PDF-1.4 fake content",
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        _, count = email.run(client, storage, {}, config, _NO_CONVERTERS)
        assert count == 1

        att_files = storage.list_files("emails")
        att_paths = [f for f in att_files if "attachments/report.pdf" in f]
        assert len(att_paths) == 1
        client.close()

    def test_zone_identifier_attachment_skipped(
        self, httpx_mock: HTTPXMock, tmp_path, graph_config
    ):
        """Attachments with ':' in name (Zone.Identifier artifacts) are skipped."""
        config = _attachment_config()
        httpx_mock.add_response(
            url=re.compile(r".*/me/mailFolders/Inbox/messages/delta.*"),
            json={
                "value": [self._make_email_msg()],
                "@odata.deltaLink": "https://delta?token=zone",
            },
        )
        httpx_mock.add_response(
            url=re.compile(r".*/me/messages/msg-with-attachment/attachments.*"),
            json={
                "value": [
                    {
                        "id": "att-zone",
                        "name": "Image.png:Zone.Identifier",
                        "contentType": "application/octet-stream",
                        "size": 100,
                        "isInline": False,
                        "@microsoft.graph.downloadUrl": "https://cdn.example.com/zone",
                    }
                ]
            },
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        _, count = email.run(client, storage, {}, config, _NO_CONVERTERS)
        assert count == 1

        # No attachment files written
        all_files = storage.list_files("emails")
        att_paths = [f for f in all_files if "attachments/" in f]
        assert len(att_paths) == 0
        client.close()

    def test_inline_attachment_skipped(
        self, httpx_mock: HTTPXMock, tmp_path, graph_config
    ):
        """Inline attachments (embedded images) are skipped."""
        config = _attachment_config()
        httpx_mock.add_response(
            url=re.compile(r".*/me/mailFolders/Inbox/messages/delta.*"),
            json={
                "value": [self._make_email_msg()],
                "@odata.deltaLink": "https://delta?token=inline",
            },
        )
        httpx_mock.add_response(
            url=re.compile(r".*/me/messages/msg-with-attachment/attachments.*"),
            json={
                "value": [
                    {
                        "id": "att-inline",
                        "name": "logo.png",
                        "contentType": "image/png",
                        "size": 2048,
                        "isInline": True,
                        "@microsoft.graph.downloadUrl": "https://cdn.example.com/logo.png",
                    }
                ]
            },
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        _, count = email.run(client, storage, {}, config, _NO_CONVERTERS)
        assert count == 1

        all_files = storage.list_files("emails")
        att_paths = [f for f in all_files if "attachments/" in f]
        assert len(att_paths) == 0
        client.close()

    def test_oversized_attachment_skipped(
        self, httpx_mock: HTTPXMock, tmp_path, graph_config
    ):
        """Attachments exceeding max_attachment_size_mb are skipped."""
        config = _attachment_config()
        httpx_mock.add_response(
            url=re.compile(r".*/me/mailFolders/Inbox/messages/delta.*"),
            json={
                "value": [self._make_email_msg()],
                "@odata.deltaLink": "https://delta?token=big",
            },
        )
        httpx_mock.add_response(
            url=re.compile(r".*/me/messages/msg-with-attachment/attachments.*"),
            json={
                "value": [
                    {
                        "id": "att-big",
                        "name": "huge.zip",
                        "contentType": "application/zip",
                        "size": 30 * 1024 * 1024,  # 30 MB — over the 25 MB limit
                        "isInline": False,
                        "@microsoft.graph.downloadUrl": "https://cdn.example.com/huge.zip",
                    }
                ]
            },
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        _, count = email.run(client, storage, {}, config, _NO_CONVERTERS)
        assert count == 1

        all_files = storage.list_files("emails")
        att_paths = [f for f in all_files if "attachments/" in f]
        assert len(att_paths) == 0
        client.close()

    def test_download_attachments_false_skips_fetch(
        self, httpx_mock: HTTPXMock, tmp_path, graph_config, email_config
    ):
        """When download_attachments=False, attachments endpoint is never called."""
        # email_config fixture has download_attachments=False
        httpx_mock.add_response(
            url=re.compile(r".*/me/mailFolders/Inbox/messages/delta.*"),
            json={
                "value": [self._make_email_msg(has_attachments=True)],
                "@odata.deltaLink": "https://delta?token=nodl",
            },
        )
        # No mock for attachments endpoint — if called, httpx_mock would raise

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        _, count = email.run(client, storage, {}, email_config, _NO_CONVERTERS)
        assert count == 1
        client.close()

    def test_attachment_without_download_url_skipped(
        self, httpx_mock: HTTPXMock, tmp_path, graph_config
    ):
        """Attachment with no @microsoft.graph.downloadUrl is skipped (no crash)."""
        config = _attachment_config()
        httpx_mock.add_response(
            url=re.compile(r".*/me/mailFolders/Inbox/messages/delta.*"),
            json={
                "value": [self._make_email_msg()],
                "@odata.deltaLink": "https://delta?token=nourl",
            },
        )
        httpx_mock.add_response(
            url=re.compile(r".*/me/messages/msg-with-attachment/attachments.*"),
            json={
                "value": [
                    {
                        "id": "att-nourl",
                        "name": "doc.docx",
                        "contentType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        "size": 512,
                        "isInline": False,
                        # No @microsoft.graph.downloadUrl
                    }
                ]
            },
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        _, count = email.run(client, storage, {}, config, _NO_CONVERTERS)
        assert count == 1

        all_files = storage.list_files("emails")
        att_paths = [f for f in all_files if "attachments/" in f]
        assert len(att_paths) == 0
        client.close()
