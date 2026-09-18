"""Tests for the single-email writer extracted into extractors/_email_writer.py."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from m365_brain.config import EmailExtractorConfig, MailboxConfig
from m365_brain.m365.extractors._email_writer import EmailSyncContext, write_email
from m365_brain.storage.local import LocalBackend


@pytest.fixture()
def email_config() -> EmailExtractorConfig:
    return EmailExtractorConfig(
        enabled=True,
        poll_interval_minutes=3,
        mailboxes=[MailboxConfig(address="me", folders=["Inbox"], output_subdir="")],
        max_items_per_sync=100,
        download_attachments=False,
        max_attachment_size_mb=25,
        attachment_convert_extensions=[],
    )


@pytest.fixture()
def sample_msg() -> dict:
    return {
        "id": "msg-001",
        "conversationId": "conv-001",
        "subject": "Test Subject",
        "receivedDateTime": "2026-01-15T10:30:00Z",
        "from": {"emailAddress": {"address": "alice@example.com", "name": "Alice"}},
        "toRecipients": [{"emailAddress": {"address": "bob@example.com", "name": "Bob"}}],
        "body": {"contentType": "text", "content": "Hello there."},
        "importance": "normal",
        "hasAttachments": False,
        "webLink": "https://outlook.office365.com/owa/?ItemID=msg-001",
    }


def _make_sync_ctx(tmp_path, email_config, ctx, seen_keys=None, path_map=None) -> tuple[EmailSyncContext, LocalBackend]:
    storage = LocalBackend(str(tmp_path / "vault"))
    client = MagicMock()
    return EmailSyncContext(
        storage=storage,
        client=client,
        config=email_config,
        ctx=ctx,
        seen_keys=seen_keys if seen_keys is not None else set(),
        path_map=path_map if path_map is not None else {},
    ), storage


class TestWriteEmail:
    def test_writes_markdown_file(self, tmp_path, email_config, sample_msg, ctx):
        sync_ctx, storage = _make_sync_ctx(tmp_path, email_config, ctx)

        result = write_email(
            sync_ctx,
            msg=sample_msg,
            folder="Inbox",
            address="me",
            output_subdir="",
            endpoint_base="/me/mailFolders/Inbox/messages",
        )

        assert result is True
        assert "msg-001" in sync_ctx.path_map
        files = storage.list_files(ctx.paths.inbox_root("email"))
        assert len(files) == 1
        content = storage.read_file(files[0])
        assert "Test Subject" in content
        assert "alice@example.com" in content

    def test_skips_invalid_message(self, tmp_path, email_config, ctx):
        sync_ctx, _ = _make_sync_ctx(tmp_path, email_config, ctx)
        msg = {"id": "", "receivedDateTime": ""}

        result = write_email(
            sync_ctx,
            msg=msg,
            folder="Inbox",
            address="me",
            output_subdir="",
            endpoint_base="/me/mailFolders/Inbox/messages",
        )
        assert result is False

    def test_skips_duplicate(self, tmp_path, email_config, sample_msg, ctx):
        seen: set[tuple[str, str]] = set()
        sync_ctx, _ = _make_sync_ctx(tmp_path, email_config, ctx, seen_keys=seen)

        write_email(
            sync_ctx,
            msg=sample_msg,
            folder="Inbox",
            address="me",
            output_subdir="",
            endpoint_base="/me/mailFolders/Inbox/messages",
        )

        result = write_email(
            sync_ctx,
            msg=sample_msg,
            folder="Inbox",
            address="me",
            output_subdir="",
            endpoint_base="/me/mailFolders/Inbox/messages",
        )
        assert result is False

    def test_html_body_converted_to_markdown(self, tmp_path, email_config, sample_msg, ctx):
        sync_ctx, storage = _make_sync_ctx(tmp_path, email_config, ctx)
        sample_msg["body"] = {"contentType": "html", "content": "<p>Hello <b>world</b></p>"}

        write_email(
            sync_ctx,
            msg=sample_msg,
            folder="Inbox",
            address="me",
            output_subdir="",
            endpoint_base="/me/mailFolders/Inbox/messages",
        )

        files = storage.list_files(ctx.paths.inbox_root("email"))
        content = storage.read_file(files[0])
        assert "<p>" not in content
        assert "world" in content

    def test_no_subject_uses_placeholder(self, tmp_path, email_config, sample_msg, ctx):
        sync_ctx, storage = _make_sync_ctx(tmp_path, email_config, ctx)
        sample_msg["subject"] = None

        write_email(
            sync_ctx,
            msg=sample_msg,
            folder="Inbox",
            address="me",
            output_subdir="",
            endpoint_base="/me/mailFolders/Inbox/messages",
        )

        files = storage.list_files(ctx.paths.inbox_root("email"))
        content = storage.read_file(files[0])
        assert "(no subject)" in content
