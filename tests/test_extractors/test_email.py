"""Tests for email extractor."""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from pytest_httpx import HTTPXMock

from m365_extract.config import EmailExtractorConfig, GraphConfig, MailboxConfig
from m365_extract.extractors import email
from m365_extract.graph_client import GraphApiError, GraphClient
from m365_extract.storage.local import LocalBackend

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"

# Empty converters config used by tests that don't exercise conversion
_NO_CONVERTERS: dict = {}


@pytest.fixture()
def email_config():
    return EmailExtractorConfig(
        enabled=True,
        poll_interval_minutes=3,
        mailboxes=[
            MailboxConfig(address="me", folders=["Inbox"], output_subdir=""),
        ],
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
        assert "delta_link_me_Inbox" in state
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

        existing_state = {"delta_link_me_Inbox": delta_url}
        state, count = email.run(client, storage, existing_state, email_config, _NO_CONVERTERS)

        assert count == 1
        assert state["delta_link_me_Inbox"] == "https://graph.microsoft.com/v1.0/delta?token=new"
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
            mailboxes=[
                MailboxConfig(address="me", folders=["Inbox", "SentItems"], output_subdir=""),
            ],
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
        assert "delta_link_me_Inbox" in state
        assert "delta_link_me_SentItems" in state
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
            email.run(client, storage, {"delta_link_me_Inbox": delta_url}, email_config, _NO_CONVERTERS)

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

    def test_duplicate_within_run_is_skipped(self, httpx_mock: HTTPXMock, tmp_path, graph_config, email_config):
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

    def test_different_minute_not_deduplicated(self, httpx_mock: HTTPXMock, tmp_path, graph_config, email_config):
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

    def test_different_subject_not_deduplicated(self, httpx_mock: HTTPXMock, tmp_path, graph_config, email_config):
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
        mailboxes=[
            MailboxConfig(address="me", folders=["Inbox"], output_subdir=""),
        ],
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

    def test_attachment_binary_written(self, httpx_mock: HTTPXMock, tmp_path, graph_config):
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

    def test_zone_identifier_attachment_skipped(self, httpx_mock: HTTPXMock, tmp_path, graph_config):
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

    def test_inline_attachment_skipped(self, httpx_mock: HTTPXMock, tmp_path, graph_config):
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

    def test_oversized_attachment_skipped(self, httpx_mock: HTTPXMock, tmp_path, graph_config):
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

    def test_download_attachments_false_skips_fetch(self, httpx_mock: HTTPXMock, tmp_path, graph_config, email_config):
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

    def test_attachment_without_download_url_skipped(self, httpx_mock: HTTPXMock, tmp_path, graph_config):
        """Attachment with no downloadUrl and no contentBytes is skipped (no crash)."""
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
                        # No @microsoft.graph.downloadUrl and no contentBytes
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

    def test_attachment_content_bytes_fallback(self, httpx_mock: HTTPXMock, tmp_path, graph_config):
        """Attachment with contentBytes (base64) is decoded and written when no downloadUrl."""
        import base64

        config = _attachment_config()
        fake_content = b"fake xlsx content"
        encoded = base64.b64encode(fake_content).decode()
        httpx_mock.add_response(
            url=re.compile(r".*/me/mailFolders/Inbox/messages/delta.*"),
            json={
                "value": [self._make_email_msg()],
                "@odata.deltaLink": "https://delta?token=cb",
            },
        )
        httpx_mock.add_response(
            url=re.compile(r".*/me/messages/msg-with-attachment/attachments.*"),
            json={
                "value": [
                    {
                        "id": "att-cb",
                        "name": "data.xlsx",
                        "contentType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        "size": len(fake_content),
                        "isInline": False,
                        "contentBytes": encoded,
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
        att_paths = [f for f in all_files if "attachments/data.xlsx" in f]
        assert len(att_paths) == 1
        client.close()

    def test_attachment_download_failure_logs_warning(self, httpx_mock: HTTPXMock, tmp_path, graph_config):
        """When client.get_bytes raises a caught error, the failure is logged and other attachments continue."""
        config = _attachment_config()
        httpx_mock.add_response(
            url=re.compile(r".*/me/mailFolders/Inbox/messages/delta.*"),
            json={
                "value": [self._make_email_msg()],
                "@odata.deltaLink": "https://delta?token=fail",
            },
        )
        httpx_mock.add_response(
            url=re.compile(r".*/me/messages/msg-with-attachment/attachments.*"),
            json={
                "value": [
                    {
                        "id": "att-fail",
                        "name": "broken.pdf",
                        "contentType": "application/pdf",
                        "size": 256,
                        "isInline": False,
                        "@microsoft.graph.downloadUrl": "https://attachments.office.com/broken.pdf",
                    }
                ]
            },
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        warnings: list[dict] = []

        def capture_warning(event, **kwargs):
            warnings.append({"event": event, **kwargs})

        with (
            patch.object(client, "get_bytes", side_effect=OSError("network error")),
            patch.object(email.log, "warning", side_effect=capture_warning),
        ):
            _, count = email.run(client, storage, {}, config, _NO_CONVERTERS)

        assert count == 1
        download_failures = [w for w in warnings if w["event"] == "email.attachment_download_failed"]
        assert len(download_failures) == 1
        assert download_failures[0]["name"] == "broken.pdf"
        assert "network error" in download_failures[0]["error"]

        # No attachment file should have been written
        all_files = storage.list_files("emails")
        att_paths = [f for f in all_files if "attachments/" in f]
        assert len(att_paths) == 0
        client.close()

    def test_attachment_triggers_convert_when_extension_matches(self, httpx_mock: HTTPXMock, tmp_path, graph_config):
        """Attachment with matching extension is converted via _convert_and_store."""
        config = EmailExtractorConfig(
            enabled=True,
            poll_interval_minutes=3,
            mailboxes=[
                MailboxConfig(address="me", folders=["Inbox"], output_subdir=""),
            ],
            lookback_days=30,
            max_items_per_sync=100,
            download_attachments=True,
            max_attachment_size_mb=25,
            attachment_convert_extensions=[".pdf"],
        )
        httpx_mock.add_response(
            url=re.compile(r".*/me/mailFolders/Inbox/messages/delta.*"),
            json={
                "value": [self._make_email_msg()],
                "@odata.deltaLink": "https://delta?token=conv",
            },
        )
        httpx_mock.add_response(
            url=re.compile(r".*/me/messages/msg-with-attachment/attachments.*"),
            json={
                "value": [
                    {
                        "id": "att-conv",
                        "name": "report.pdf",
                        "contentType": "application/pdf",
                        "size": 64,
                        "isInline": False,
                        "@microsoft.graph.downloadUrl": "https://attachments.office.com/report.pdf",
                    }
                ]
            },
        )
        httpx_mock.add_response(
            url="https://attachments.office.com/report.pdf",
            content=b"%PDF-1.4 fake pdf",
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        with patch.object(email, "convert_document", return_value="# Converted\n\nbody") as mock_conv:
            _, count = email.run(client, storage, {}, config, _NO_CONVERTERS)

        assert count == 1
        assert mock_conv.call_count == 1

        all_files = storage.list_files("emails")
        converted = [f for f in all_files if "attachments_converted/report.pdf.md" in f]
        assert len(converted) == 1
        assert "# Converted" in storage.read_file(converted[0])
        client.close()


# ---------------------------------------------------------------------------
# Multi-mailbox routing
# ---------------------------------------------------------------------------


class TestSharedMailbox:
    """Verify the shared mailbox path uses /users/{address}/... and namespaces storage."""

    @pytest.fixture(autouse=True)
    def _clear_folder_cache(self):
        email._resolved_folder_ids.clear()
        yield
        email._resolved_folder_ids.clear()

    def _config(self, mailboxes: list[MailboxConfig]) -> EmailExtractorConfig:
        return EmailExtractorConfig(
            enabled=True,
            poll_interval_minutes=3,
            mailboxes=mailboxes,
            lookback_days=30,
            max_items_per_sync=100,
            download_attachments=False,
            max_attachment_size_mb=25,
            attachment_convert_extensions=[],
        )

    def _msg(self, msg_id: str, subject: str, received: str) -> dict:
        return {
            "id": msg_id,
            "subject": subject,
            "body": {"contentType": "text", "content": "body"},
            "from": {"emailAddress": {"name": "S", "address": "s@example.com"}},
            "toRecipients": [],
            "receivedDateTime": received,
            "importance": "normal",
            "hasAttachments": False,
            "webLink": "",
            "parentFolderId": "inbox",
        }

    def test_shared_mailbox_uses_users_endpoint_and_subdir(self, httpx_mock: HTTPXMock, tmp_path, graph_config):
        config = self._config(
            [MailboxConfig(address="ai@sanoptis.com", folders=["Inbox"], output_subdir="ai-sanoptis")]
        )
        httpx_mock.add_response(
            url=re.compile(r".*/users/ai@sanoptis\.com/mailFolders/Inbox/messages/delta.*"),
            json={
                "value": [self._msg("msg-shared-1", "Hello shared", "2026-05-08T10:00:00Z")],
                "@odata.deltaLink": "https://delta?token=shared",
            },
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        state, count = email.run(client, storage, {}, config, _NO_CONVERTERS)

        assert count == 1
        assert "delta_link_ai@sanoptis.com_Inbox" in state

        # Storage path must be namespaced under the output_subdir
        files = storage.list_files("emails")
        assert any("emails/ai-sanoptis/2026/2026-05-08/" in f for f in files)
        # And NOT placed at the top-level emails/{year}/...
        assert not any(f.startswith("emails/2026/") for f in files)

        # Frontmatter must record the mailbox address
        content = storage.read_file(files[0])
        from m365_extract.markdown_writer import loads_markdown

        fm, _ = loads_markdown(content)
        assert fm["mailbox"] == "ai@sanoptis.com"
        client.close()

    def test_personal_and_shared_isolated_in_one_run(self, httpx_mock: HTTPXMock, tmp_path, graph_config):
        config = self._config(
            [
                MailboxConfig(address="me", folders=["Inbox"], output_subdir=""),
                MailboxConfig(address="ai@sanoptis.com", folders=["Inbox"], output_subdir="ai-sanoptis"),
            ]
        )
        httpx_mock.add_response(
            url=re.compile(r".*/me/mailFolders/Inbox/messages/delta.*"),
            json={
                "value": [self._msg("msg-personal", "Personal", "2026-05-08T09:00:00Z")],
                "@odata.deltaLink": "https://delta?token=me",
            },
        )
        httpx_mock.add_response(
            url=re.compile(r".*/users/ai@sanoptis\.com/mailFolders/Inbox/messages/delta.*"),
            json={
                "value": [self._msg("msg-shared", "Shared", "2026-05-08T10:00:00Z")],
                "@odata.deltaLink": "https://delta?token=ai",
            },
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        state, count = email.run(client, storage, {}, config, _NO_CONVERTERS)

        assert count == 2
        assert "delta_link_me_Inbox" in state
        assert "delta_link_ai@sanoptis.com_Inbox" in state

        files = storage.list_files("emails")
        personal = [f for f in files if f.startswith("emails/2026/")]
        shared = [f for f in files if f.startswith("emails/ai-sanoptis/")]
        assert len(personal) == 1
        assert len(shared) == 1
        client.close()

    def test_auto_discover_filters_system_folders(self, httpx_mock: HTTPXMock, tmp_path, graph_config):
        """When folders=None, discovery uses GET /mailFolders and skips Drafts/Junk/etc."""
        config = self._config([MailboxConfig(address="ai@sanoptis.com", folders=None, output_subdir="ai-sanoptis")])

        # Discovery response — mix of keep + skip folders.
        # URL params are encoded ($select=id%2CdisplayName...), so match loosely on
        # the listing endpoint, distinguished from /mailFolders/{id}/messages by the
        # trailing `?` indicating a query-string list call rather than a sub-resource.
        # Graph API v1.0 returns `isHidden`; `wellKnownName` is beta-only and not selected.
        httpx_mock.add_response(
            url=re.compile(r".*/users/ai@sanoptis\.com/mailFolders\?.*"),
            json={
                "value": [
                    {"id": "id-inbox", "displayName": "Inbox", "isHidden": False},
                    {"id": "id-drafts", "displayName": "Drafts", "isHidden": False},
                    {"id": "id-junk", "displayName": "Junk Email", "isHidden": False},
                    {"id": "id-projects", "displayName": "Projects", "isHidden": False},
                    {"id": "id-deleted", "displayName": "Deleted Items", "isHidden": False},
                    {"id": "id-hidden", "displayName": "Internal", "isHidden": True},
                ]
            },
        )

        # Inbox delta (uses well-known "Inbox" as the folder ID)
        httpx_mock.add_response(
            url=re.compile(r".*/users/ai@sanoptis\.com/mailFolders/Inbox/messages/delta.*"),
            json={
                "value": [self._msg("m-1", "in inbox", "2026-05-08T10:00:00Z")],
                "@odata.deltaLink": "https://delta?token=inbox",
            },
        )
        # Projects delta — uses the resolved folder id from discovery cache
        httpx_mock.add_response(
            url=re.compile(r".*/users/ai@sanoptis\.com/mailFolders/id-projects/messages/delta.*"),
            json={
                "value": [self._msg("m-2", "in projects", "2026-05-08T10:00:00Z")],
                "@odata.deltaLink": "https://delta?token=projects",
            },
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        state, count = email.run(client, storage, {}, config, _NO_CONVERTERS)

        # Only Inbox + Projects synced; Drafts/Junk/Deleted skipped by displayName,
        # Internal skipped by isHidden=true.
        assert count == 2
        assert "delta_link_ai@sanoptis.com_Inbox" in state
        assert "delta_link_ai@sanoptis.com_Projects" in state
        assert "delta_link_ai@sanoptis.com_Drafts" not in state
        assert "delta_link_ai@sanoptis.com_Junk Email" not in state
        assert "delta_link_ai@sanoptis.com_Deleted Items" not in state
        assert "delta_link_ai@sanoptis.com_Internal" not in state
        client.close()


# ---------------------------------------------------------------------------
# Custom folder resolution (_resolve_folder_id)
# ---------------------------------------------------------------------------


class TestResolveFolderId:
    """Tests for _resolve_folder_id: well-known folders, Graph API lookup, and caching."""

    @pytest.fixture(autouse=True)
    def _clear_folder_cache(self):
        """Reset module-level cache before each test to avoid ordering dependencies."""
        email._resolved_folder_ids.clear()
        yield
        email._resolved_folder_ids.clear()

    def test_well_known_folder_returns_predefined_id(self):
        """Well-known folders (Inbox, SentItems, etc.) return their predefined ID without calling the API."""
        client = MagicMock(spec=GraphClient)
        assert email._resolve_folder_id(client, "me", "Inbox") == "Inbox"
        assert email._resolve_folder_id(client, "me", "SentItems") == "SentItems"
        client.get.assert_not_called()

    def test_custom_folder_resolved_via_graph_api(self):
        """Custom folder name is resolved to its ID via Graph API query."""
        client = MagicMock(spec=GraphClient)
        client.get.return_value = {"value": [{"id": "abc123", "displayName": "Archive-Custom"}]}

        result = email._resolve_folder_id(client, "me", "Archive-Custom")

        assert result == "abc123"
        client.get.assert_called_once_with(
            "/me/mailFolders",
            {"$filter": "displayName eq 'Archive-Custom'", "$select": "id,displayName", "$top": "1"},
        )

    def test_custom_folder_cached_after_first_resolution(self):
        """Second call with the same custom folder name uses cache — Graph API not called again."""
        client = MagicMock(spec=GraphClient)
        client.get.return_value = {"value": [{"id": "folder-xyz", "displayName": "Projects"}]}

        first = email._resolve_folder_id(client, "me", "Projects")
        second = email._resolve_folder_id(client, "me", "Projects")

        assert first == "folder-xyz"
        assert second == "folder-xyz"
        assert client.get.call_count == 1

    def test_custom_folder_not_found_raises_graph_api_error(self):
        """Empty response from Graph API raises GraphApiError with helpful message."""
        client = MagicMock(spec=GraphClient)
        client.get.return_value = {"value": []}

        with pytest.raises(GraphApiError, match="Mail folder not found: 'NonExistent'"):
            email._resolve_folder_id(client, "me", "NonExistent")

    def test_not_found_folder_not_cached(self):
        """Failed resolution does not pollute the cache."""
        client = MagicMock(spec=GraphClient)
        client.get.return_value = {"value": []}

        with pytest.raises(GraphApiError):
            email._resolve_folder_id(client, "me", "Ghost")

        assert ("me", "Ghost") not in email._resolved_folder_ids

    def test_custom_folder_cache_keyed_by_mailbox(self):
        """The same folder name in different mailboxes resolves independently."""
        client = MagicMock(spec=GraphClient)
        client.get.side_effect = [
            {"value": [{"id": "id-personal", "displayName": "Projects"}]},
            {"value": [{"id": "id-shared", "displayName": "Projects"}]},
        ]

        first = email._resolve_folder_id(client, "me", "Projects")
        second = email._resolve_folder_id(client, "ai@sanoptis.com", "Projects")

        assert first == "id-personal"
        assert second == "id-shared"
        assert client.get.call_count == 2
        # The second call must use the /users/... endpoint base.
        assert client.get.call_args_list[1].args[0] == "/users/ai@sanoptis.com/mailFolders"


class TestNarrowedExceptionHandling:
    """Verify that narrowed except clauses catch expected errors but propagate programming errors."""

    def test_download_graph_api_error_caught(self, tmp_path):
        """GraphApiError during attachment download is caught (log-and-continue)."""
        storage = LocalBackend(str(tmp_path / "vault"))
        client = MagicMock(spec=GraphClient)
        client.get_paginated.return_value = iter(
            [{"name": "file.pdf", "size": 100, "isInline": False, "@microsoft.graph.downloadUrl": "https://cdn/f"}]
        )
        client.get_bytes.side_effect = GraphApiError("404 Not Found")

        config = _attachment_config()
        email._download_attachments(client, storage, "me", "msg-1", "emails/2026/dir", config, _NO_CONVERTERS)

    def test_fetch_attachments_graph_api_error_caught(self, tmp_path):
        """GraphApiError during attachment list fetch is caught (log-and-continue)."""
        storage = LocalBackend(str(tmp_path / "vault"))
        client = MagicMock(spec=GraphClient)
        client.get_paginated.side_effect = GraphApiError("500 Server Error")

        config = _attachment_config()
        email._download_attachments(client, storage, "me", "msg-1", "emails/2026/dir", config, _NO_CONVERTERS)

    def test_download_type_error_propagates(self, tmp_path):
        """TypeError during attachment download propagates (programming error)."""
        storage = LocalBackend(str(tmp_path / "vault"))
        client = MagicMock(spec=GraphClient)
        client.get_paginated.return_value = iter(
            [{"name": "file.pdf", "size": 100, "isInline": False, "@microsoft.graph.downloadUrl": "https://cdn/f"}]
        )
        client.get_bytes.side_effect = TypeError("unexpected None")

        config = _attachment_config()
        with pytest.raises(TypeError):
            email._download_attachments(client, storage, "me", "msg-1", "emails/2026/dir", config, _NO_CONVERTERS)

    def test_fetch_attachments_type_error_propagates(self, tmp_path):
        """TypeError during attachment list fetch propagates (programming error)."""
        storage = LocalBackend(str(tmp_path / "vault"))
        client = MagicMock(spec=GraphClient)
        client.get_paginated.side_effect = TypeError("bad argument")

        config = _attachment_config()
        with pytest.raises(TypeError):
            email._download_attachments(client, storage, "me", "msg-1", "emails/2026/dir", config, _NO_CONVERTERS)

    def test_convert_os_error_caught(self, tmp_path):
        """OSError during attachment conversion is caught (log-and-continue)."""
        storage = LocalBackend(str(tmp_path / "vault"))
        with patch("m365_extract.extractors.email.convert_document", side_effect=OSError("disk full")):
            email._convert_and_store(storage, b"data", "file.pdf", "emails/dir", _NO_CONVERTERS)

    def test_convert_attribute_error_propagates(self, tmp_path):
        """AttributeError during conversion propagates (programming error)."""
        storage = LocalBackend(str(tmp_path / "vault"))
        with (
            patch("m365_extract.extractors.email.convert_document", side_effect=AttributeError("oops")),
            pytest.raises(AttributeError),
        ):
            email._convert_and_store(storage, b"data", "file.pdf", "emails/dir", _NO_CONVERTERS)


# ---------------------------------------------------------------------------
# _convert_and_store unit tests
# ---------------------------------------------------------------------------


class TestConvertAndStore:
    """Unit tests for _convert_and_store covering happy path, failure, and tmp cleanup."""

    def test_happy_path_writes_markdown(self, tmp_path):
        """convert_document returns markdown; storage.write_file gets correct path/content."""
        storage = MagicMock()
        with patch.object(email, "convert_document", return_value="# Hello\n\ncontent") as mock_conv:
            email._convert_and_store(
                storage=storage,
                data=b"binary-data",
                att_name="report.pdf",
                email_dir="emails/2026/2026-03-12/sub-abc123",
                converters_config={},
            )

        mock_conv.assert_called_once()
        called_path = mock_conv.call_args.args[0]
        assert isinstance(called_path, Path)
        assert called_path.suffix == ".pdf"

        storage.write_file.assert_called_once_with(
            "emails/2026/2026-03-12/sub-abc123/attachments_converted/report.pdf.md",
            "# Hello\n\ncontent",
        )

    def test_conversion_failure_logs_warning_no_raise(self, tmp_path):
        """When convert_document raises a caught error, the warning is logged and no exception escapes."""
        storage = MagicMock()
        warnings: list[dict] = []

        def capture(event, **kwargs):
            warnings.append({"event": event, **kwargs})

        with (
            patch.object(email, "convert_document", side_effect=OSError("bad pdf")),
            patch.object(email.log, "warning", side_effect=capture),
        ):
            email._convert_and_store(
                storage=storage,
                data=b"junk",
                att_name="bad.pdf",
                email_dir="emails/2026/2026-03-12/dir",
                converters_config={},
            )

        # storage.write_file must NOT have been called when conversion fails
        storage.write_file.assert_not_called()

        convert_failures = [w for w in warnings if w["event"] == "email.attachment_convert_failed"]
        assert len(convert_failures) == 1
        assert convert_failures[0]["name"] == "bad.pdf"
        assert "bad pdf" in convert_failures[0]["error"]

    def test_tmp_path_cleaned_up_on_failure(self, tmp_path):
        """tmp file is deleted by the finally block even when convert_document raises a caught error."""
        storage = MagicMock()
        captured_paths: list[Path] = []

        def capture_path_and_raise(path: Path, _config: dict) -> str:
            captured_paths.append(path)
            assert path.exists(), "tmp file should exist when convert_document is invoked"
            raise OSError("conversion blew up")

        with patch.object(email, "convert_document", side_effect=capture_path_and_raise):
            email._convert_and_store(
                storage=storage,
                data=b"bytes",
                att_name="doc.docx",
                email_dir="emails/2026/2026-03-12/dir",
                converters_config={},
            )

        assert len(captured_paths) == 1
        # finally block must have unlinked the tmp file
        assert not captured_paths[0].exists()

    def test_tmp_path_cleaned_up_on_success(self, tmp_path):
        """tmp file is deleted by the finally block on the happy path too."""
        storage = MagicMock()
        captured_paths: list[Path] = []

        def capture_path(path: Path, _config: dict) -> str:
            captured_paths.append(path)
            return "# md"

        with patch.object(email, "convert_document", side_effect=capture_path):
            email._convert_and_store(
                storage=storage,
                data=b"bytes",
                att_name="doc.docx",
                email_dir="emails/2026/2026-03-12/dir",
                converters_config={},
            )

        assert len(captured_paths) == 1
        assert not captured_paths[0].exists()
