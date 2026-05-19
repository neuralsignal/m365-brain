"""Tests for the Teams attachment + hostedContents helpers."""

from __future__ import annotations

import base64
import re
from unittest.mock import patch

import httpx
import pytest
from pytest_httpx import HTTPXMock

from m365_extract.config import GraphConfig, TeamsChatsExtractorConfig
from m365_extract.extractors import _teams_attachment_helpers as helpers
from m365_extract.graph_client import GraphApiError, GraphClient
from m365_extract.storage.local import LocalBackend


@pytest.fixture()
def graph_config() -> GraphConfig:
    return GraphConfig(
        max_retries=1,
        backoff_base_ms=10,
        timeout_seconds=5,
        max_pages=5,
        max_retry_after_seconds=300.0,
        error_message_max_length=200,
    )


def _config(*, convert: list[str] | None = None, max_mb: int = 100) -> TeamsChatsExtractorConfig:
    return TeamsChatsExtractorConfig(
        enabled=True,
        poll_interval_minutes=5,
        max_messages_per_chat=200,
        download_attachments=True,
        download_inline_images=True,
        max_attachment_size_mb=max_mb,
        attachment_convert_extensions=convert if convert is not None else [],
    )


class TestEncodeShareUrl:
    def test_roundtrip(self) -> None:
        url = "https://sanoptis.sharepoint.com/sites/x/Shared Documents/spec.pdf"
        encoded = helpers._encode_share_url(url)
        assert encoded.startswith("u!")
        # Re-pad and decode to verify it round-trips to the original URL
        body = encoded[2:]
        padding = "=" * (-len(body) % 4)
        decoded = base64.urlsafe_b64decode(body + padding).decode("utf-8")
        assert decoded == url


class TestDownloadMessageAttachments:
    def test_reference_attachment_written(self, httpx_mock: HTTPXMock, tmp_path, graph_config) -> None:
        content_url = "https://sanoptis.sharepoint.com/sites/x/spec.pdf"
        encoded = helpers._encode_share_url(content_url)
        httpx_mock.add_response(
            url=re.compile(rf".*/shares/{re.escape(encoded)}/driveItem.*"),
            json={
                "id": "drive-item-1",
                "size": 1024,
                "@microsoft.graph.downloadUrl": "https://sanoptis.sharepoint.com/_layouts/download.aspx?token=abc",
            },
        )
        httpx_mock.add_response(
            url=re.compile(r"https://sanoptis\.sharepoint\.com/_layouts/download\.aspx.*"),
            content=b"%PDF-1.4 fake",
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")
        msg = {
            "id": "msg-1",
            "attachments": [
                {
                    "contentType": "reference",
                    "name": "spec.pdf",
                    "contentUrl": content_url,
                }
            ],
        }

        refs = helpers.download_message_attachments(client, storage, msg, "teams-chats/foo_abc", _config(), {})

        assert len(refs) == 1
        assert refs[0].name == "spec.pdf"
        assert refs[0].relative_path == "attachments/msg-1/spec.pdf"
        assert refs[0].converted_path is None
        files = storage.list_files("teams-chats")
        assert "teams-chats/foo_abc/attachments/msg-1/spec.pdf" in files
        client.close()

    def test_skipped_reference_types_emit_no_request(self, httpx_mock: HTTPXMock, tmp_path, graph_config) -> None:
        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")
        msg = {
            "id": "msg-2",
            "attachments": [
                {"contentType": "messageReference", "name": "n", "contentUrl": "https://x"},
                {"contentType": "meetingReference", "name": "m", "contentUrl": "https://y"},
            ],
        }

        refs = helpers.download_message_attachments(client, storage, msg, "teams-chats/foo_abc", _config(), {})

        assert refs == []
        assert httpx_mock.get_requests() == []
        client.close()

    def test_oversized_attachment_skipped(self, httpx_mock: HTTPXMock, tmp_path, graph_config) -> None:
        content_url = "https://sanoptis.sharepoint.com/sites/x/huge.zip"
        encoded = helpers._encode_share_url(content_url)
        httpx_mock.add_response(
            url=re.compile(rf".*/shares/{re.escape(encoded)}/driveItem.*"),
            json={
                "id": "drive-item-big",
                "size": 200 * 1024 * 1024,
                "@microsoft.graph.downloadUrl": "https://sanoptis.sharepoint.com/dl?token=z",
            },
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")
        msg = {
            "id": "msg-3",
            "attachments": [{"contentType": "reference", "name": "huge.zip", "contentUrl": content_url}],
        }

        refs = helpers.download_message_attachments(
            client, storage, msg, "teams-chats/foo_abc", _config(max_mb=100), {}
        )

        assert refs == []
        assert storage.list_files("teams-chats") == []
        client.close()

    def test_missing_download_url_skipped(self, httpx_mock: HTTPXMock, tmp_path, graph_config) -> None:
        content_url = "https://sanoptis.sharepoint.com/sites/x/nourl.pdf"
        encoded = helpers._encode_share_url(content_url)
        httpx_mock.add_response(
            url=re.compile(rf".*/shares/{re.escape(encoded)}/driveItem.*"),
            json={"id": "drive-item", "size": 100},
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")
        msg = {
            "id": "msg-4",
            "attachments": [{"contentType": "reference", "name": "nourl.pdf", "contentUrl": content_url}],
        }

        refs = helpers.download_message_attachments(client, storage, msg, "teams-chats/foo_abc", _config(), {})

        assert refs == []
        client.close()

    def test_missing_name_or_url_skipped(self, tmp_path, graph_config) -> None:
        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")
        msg = {
            "id": "msg-5",
            "attachments": [
                {"contentType": "reference", "name": "", "contentUrl": "https://x"},
                {"contentType": "reference", "name": "ok.pdf", "contentUrl": ""},
            ],
        }

        refs = helpers.download_message_attachments(client, storage, msg, "teams-chats/foo_abc", _config(), {})

        assert refs == []
        client.close()

    def test_unsupported_content_type_skipped(self, tmp_path, graph_config) -> None:
        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")
        msg = {
            "id": "msg-6",
            "attachments": [
                {
                    "contentType": "application/vnd.microsoft.card.codesnippet",
                    "name": "snippet",
                    "contentUrl": "https://example/x",
                }
            ],
        }

        refs = helpers.download_message_attachments(client, storage, msg, "teams-chats/foo_abc", _config(), {})

        assert refs == []
        client.close()

    def test_convert_called_for_matching_extension(self, httpx_mock: HTTPXMock, tmp_path, graph_config) -> None:
        content_url = "https://sanoptis.sharepoint.com/sites/x/spec.pdf"
        encoded = helpers._encode_share_url(content_url)
        httpx_mock.add_response(
            url=re.compile(rf".*/shares/{re.escape(encoded)}/driveItem.*"),
            json={
                "id": "drive-item",
                "size": 1024,
                "@microsoft.graph.downloadUrl": "https://sanoptis.sharepoint.com/dl?t=x",
            },
        )
        httpx_mock.add_response(
            url=re.compile(r"https://sanoptis\.sharepoint\.com/dl.*"),
            content=b"%PDF-1.4 fake",
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")
        msg = {
            "id": "msg-7",
            "attachments": [{"contentType": "reference", "name": "spec.pdf", "contentUrl": content_url}],
        }

        with patch.object(helpers, "convert_and_store") as mock_conv:
            refs = helpers.download_message_attachments(
                client,
                storage,
                msg,
                "teams-chats/foo_abc",
                _config(convert=[".pdf"]),
                {"backends": {"pdf": "markitdown"}},
            )

        assert mock_conv.call_count == 1
        assert refs[0].converted_path == "attachments_converted/msg-7/spec.pdf.md"
        # Verify the target_path passed to convert_and_store includes the per-message subdir
        target_path = mock_conv.call_args.args[3]
        assert target_path == "teams-chats/foo_abc/attachments_converted/msg-7/spec.pdf.md"
        client.close()

    def test_path_traversal_in_name_stripped(self, httpx_mock: HTTPXMock, tmp_path, graph_config) -> None:
        content_url = "https://sanoptis.sharepoint.com/sites/x/file"
        encoded = helpers._encode_share_url(content_url)
        httpx_mock.add_response(
            url=re.compile(rf".*/shares/{re.escape(encoded)}/driveItem.*"),
            json={
                "id": "drive-item",
                "size": 16,
                "@microsoft.graph.downloadUrl": "https://sanoptis.sharepoint.com/dl?t=x",
            },
        )
        httpx_mock.add_response(
            url=re.compile(r"https://sanoptis\.sharepoint\.com/dl.*"),
            content=b"data",
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")
        msg = {
            "id": "msg-8",
            "attachments": [{"contentType": "reference", "name": "../../escape.txt", "contentUrl": content_url}],
        }

        refs = helpers.download_message_attachments(client, storage, msg, "teams-chats/foo_abc", _config(), {})

        assert len(refs) == 1
        assert refs[0].name == "escape.txt"
        files = storage.list_files("teams-chats")
        assert "teams-chats/foo_abc/attachments/msg-8/escape.txt" in files
        # Confirm nothing escaped the vault
        assert not any(".." in f for f in files)
        client.close()

    def test_download_failure_logged_and_continued(self, httpx_mock: HTTPXMock, tmp_path, graph_config) -> None:
        content_url = "https://sanoptis.sharepoint.com/sites/x/spec.pdf"
        encoded = helpers._encode_share_url(content_url)
        httpx_mock.add_response(
            url=re.compile(rf".*/shares/{re.escape(encoded)}/driveItem.*"),
            status_code=404,
            text='{"error":{"code":"itemNotFound","message":"gone"}}',
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")
        msg = {
            "id": "msg-9",
            "attachments": [{"contentType": "reference", "name": "spec.pdf", "contentUrl": content_url}],
        }

        warnings: list[dict] = []

        def capture(event, **kwargs):
            warnings.append({"event": event, **kwargs})

        with patch.object(helpers.log, "warning", side_effect=capture):
            refs = helpers.download_message_attachments(client, storage, msg, "teams-chats/foo_abc", _config(), {})

        assert refs == []
        download_failures = [w for w in warnings if w["event"] == "teams_chats.attachment_download_failed"]
        assert len(download_failures) == 1
        client.close()


class TestDownloadInlineImages:
    def test_inline_image_written_and_mapped(self, httpx_mock: HTTPXMock, tmp_path, graph_config) -> None:
        chat_id = "19:abc"
        msg_id = "1"
        hid = "HID-1"
        httpx_mock.add_response(
            url=re.compile(rf".*/chats/{re.escape(chat_id)}/messages/{msg_id}/hostedContents.*"),
            json={"value": [{"id": hid}]},
        )
        httpx_mock.add_response(
            url=re.compile(rf".*/chats/{re.escape(chat_id)}/messages/{msg_id}/hostedContents/{hid}/\$value.*"),
            content=b"\x89PNG\r\n\x1a\n",
            headers={"Content-Type": "image/png"},
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")
        msg = {"id": msg_id}

        hosted_map = helpers.download_inline_images(client, storage, chat_id, msg, "teams-chats/foo_abc", _config())

        assert hosted_map == {hid: f"attachments/{msg_id}/inline_0.png"}
        files = storage.list_files("teams-chats")
        assert f"teams-chats/foo_abc/attachments/{msg_id}/inline_0.png" in files
        client.close()

    def test_unknown_content_type_falls_back_to_bin(self, httpx_mock: HTTPXMock, tmp_path, graph_config) -> None:
        chat_id = "19:def"
        msg_id = "2"
        hid = "HID-2"
        httpx_mock.add_response(
            url=re.compile(rf".*/chats/{re.escape(chat_id)}/messages/{msg_id}/hostedContents.*"),
            json={"value": [{"id": hid}]},
        )
        httpx_mock.add_response(
            url=re.compile(rf".*/chats/{re.escape(chat_id)}/messages/{msg_id}/hostedContents/{hid}/\$value.*"),
            content=b"unknown-bytes",
            headers={"Content-Type": "application/x-novel"},
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")
        msg = {"id": msg_id}

        hosted_map = helpers.download_inline_images(client, storage, chat_id, msg, "teams-chats/foo_abc", _config())

        assert hosted_map[hid].endswith("inline_0.bin")
        client.close()

    def test_oversized_hosted_content_skipped(self, httpx_mock: HTTPXMock, tmp_path, graph_config) -> None:
        chat_id = "19:ghi"
        msg_id = "3"
        hid = "HID-3"
        httpx_mock.add_response(
            url=re.compile(rf".*/chats/{re.escape(chat_id)}/messages/{msg_id}/hostedContents.*"),
            json={"value": [{"id": hid}]},
        )
        big = b"x" * (3 * 1024 * 1024)
        httpx_mock.add_response(
            url=re.compile(rf".*/chats/{re.escape(chat_id)}/messages/{msg_id}/hostedContents/{hid}/\$value.*"),
            content=big,
            headers={"Content-Type": "image/png"},
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")
        msg = {"id": msg_id}

        hosted_map = helpers.download_inline_images(
            client, storage, chat_id, msg, "teams-chats/foo_abc", _config(max_mb=1)
        )

        assert hosted_map == {}
        client.close()

    def test_fetch_failure_returns_empty(self, httpx_mock: HTTPXMock, tmp_path, graph_config) -> None:
        chat_id = "19:jkl"
        msg_id = "4"
        httpx_mock.add_response(
            url=re.compile(rf".*/chats/{re.escape(chat_id)}/messages/{msg_id}/hostedContents.*"),
            status_code=500,
            text='{"error":{"code":"InternalError","message":"boom"}}',
        )
        # _execute_with_retry retries on 500 — supply a second response too
        httpx_mock.add_response(
            url=re.compile(rf".*/chats/{re.escape(chat_id)}/messages/{msg_id}/hostedContents.*"),
            status_code=500,
            text='{"error":{"code":"InternalError","message":"boom"}}',
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        hosted_map = helpers.download_inline_images(
            client, storage, chat_id, {"id": msg_id}, "teams-chats/foo_abc", _config()
        )

        assert hosted_map == {}
        client.close()

    def test_download_transport_error_skipped(self, tmp_path, graph_config) -> None:
        from unittest.mock import MagicMock

        chat_id = "19:mno"
        msg_id = "5"
        storage = LocalBackend(str(tmp_path / "vault"))
        mock_client = MagicMock(spec=GraphClient)
        mock_client.max_pages = 5
        mock_client.get_paginated.return_value = iter([{"id": "HID-X"}])
        mock_client.get_bytes_with_content_type.side_effect = httpx.ConnectError("boom")

        hosted_map = helpers.download_inline_images(
            mock_client, storage, chat_id, {"id": msg_id}, "teams-chats/foo_abc", _config()
        )

        assert hosted_map == {}

    def test_no_msg_id_returns_empty(self, tmp_path, graph_config) -> None:
        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")
        assert helpers.download_inline_images(client, storage, "19:abc", {}, "teams-chats/foo_abc", _config()) == {}
        client.close()


class TestSkipsEmpty:
    def test_no_attachments_returns_empty(self, tmp_path, graph_config) -> None:
        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")
        assert (
            helpers.download_message_attachments(client, storage, {"id": "x"}, "teams-chats/foo_abc", _config(), {})
            == []
        )
        client.close()

    def test_no_msg_id_returns_empty(self, tmp_path, graph_config) -> None:
        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")
        msg = {"attachments": [{"contentType": "reference", "name": "x", "contentUrl": "https://y"}]}
        assert helpers.download_message_attachments(client, storage, msg, "teams-chats/foo_abc", _config(), {}) == []
        client.close()


class TestFraudulentDomainSSRF:
    def test_blocked_domain_raises(self, tmp_path, graph_config) -> None:
        """get_bytes refuses non-Microsoft download URLs, so the helper logs and skips."""
        from unittest.mock import MagicMock

        storage = LocalBackend(str(tmp_path / "vault"))
        client = MagicMock(spec=GraphClient)
        client.get.return_value = {
            "id": "x",
            "size": 32,
            "@microsoft.graph.downloadUrl": "https://evil.example.com/payload",
        }
        client.get_bytes.side_effect = GraphApiError("Download URL blocked: ...")

        msg = {
            "id": "msg-evil",
            "attachments": [
                {"contentType": "reference", "name": "x.pdf", "contentUrl": "https://sanoptis.sharepoint.com/x"}
            ],
        }

        refs = helpers.download_message_attachments(client, storage, msg, "teams-chats/foo_abc", _config(), {})
        assert refs == []
