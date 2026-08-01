"""Tests for ``_teams_hosted_content.download_inline_images``."""

from __future__ import annotations

from unittest.mock import MagicMock

from m365_extract.config import TeamsChatsExtractorConfig
from m365_extract.extractors._teams_hosted_content import download_inline_images
from m365_extract.storage.exceptions import StorageError


def _settings() -> TeamsChatsExtractorConfig:
    return TeamsChatsExtractorConfig(
        enabled=True,
        poll_interval_minutes=5,
        max_messages_per_chat=200,
        download_attachments=False,
        download_inline_images=True,
        max_attachment_size_mb=25,
        attachment_convert_extensions=[],
    )


def _make_client(items: list[dict]) -> MagicMock:
    client = MagicMock()
    client.get_paginated.return_value = iter(items)
    client.max_pages = 10
    client.get_bytes_with_content_type.return_value = (b"PNG-DATA", "image/png")
    return client


class TestEmptyHostedContentId:
    def test_empty_id_is_skipped(self):
        client = _make_client([{"id": ""}, {"id": "hc-valid"}])
        storage = MagicMock()
        msg = {"id": "m1"}

        result = download_inline_images(client, storage, "/chats/c1/messages/m1", msg, "conv", _settings())

        assert "hc-valid" in result
        assert len(result) == 1
        storage.write_bytes.assert_called_once()

    def test_missing_id_key_is_skipped(self):
        client = _make_client([{}, {"id": "hc-ok"}])
        storage = MagicMock()
        msg = {"id": "m1"}

        result = download_inline_images(client, storage, "/chats/c1/messages/m1", msg, "conv", _settings())

        assert "hc-ok" in result
        assert len(result) == 1


class TestStorageWriteFailure:
    def test_storage_error_skips_item_and_continues(self):
        client = _make_client([{"id": "hc1"}, {"id": "hc2"}])
        storage = MagicMock()
        storage.write_bytes.side_effect = [
            StorageError("disk full"),
            None,
        ]
        msg = {"id": "m1"}

        result = download_inline_images(client, storage, "/chats/c1/messages/m1", msg, "conv", _settings())

        assert "hc1" not in result
        assert "hc2" in result
        assert storage.write_bytes.call_count == 2

    def test_os_error_skips_item_and_continues(self):
        client = _make_client([{"id": "hc1"}, {"id": "hc2"}])
        storage = MagicMock()
        storage.write_bytes.side_effect = [
            OSError("permission denied"),
            None,
        ]
        msg = {"id": "m1"}

        result = download_inline_images(client, storage, "/chats/c1/messages/m1", msg, "conv", _settings())

        assert "hc1" not in result
        assert "hc2" in result

    def test_storage_error_logs_warning(self, caplog):
        client = _make_client([{"id": "hc1"}])
        storage = MagicMock()
        storage.write_bytes.side_effect = StorageError("boom")
        msg = {"id": "m1"}

        result = download_inline_images(client, storage, "/chats/c1/messages/m1", msg, "conv", _settings())

        assert result == {}
