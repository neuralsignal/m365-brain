"""Tests for the Teams attachment + hostedContents helpers."""

from __future__ import annotations

import base64
import os
import re
from unittest.mock import patch

import httpx
import pytest
from hypothesis import given
from hypothesis import strategies as st
from pytest_httpx import HTTPXMock

from m365_brain.config import GraphConfig, TeamsChatsExtractorConfig
from m365_brain.extractors import _teams_attachment_helpers as helpers
from m365_brain.extractors import _teams_hosted_content as hosted_content
from m365_brain.extractors._teams_context import TeamsContext
from m365_brain.graph_client import GraphApiError, GraphClient
from m365_brain.storage.local import LocalBackend


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


def _ctx(
    client,
    storage,
    conv_dir: str,
    *,
    settings=None,
    converters_config: dict | None = None,
    failed_attachments: dict[str, str] | None = None,
) -> TeamsContext:
    """Build a TeamsContext for direct helper calls."""
    return TeamsContext(
        client=client,
        storage=storage,
        settings=settings,
        converters_config=converters_config if converters_config is not None else {},
        failed_attachments=failed_attachments if failed_attachments is not None else {},
        conv_dir=conv_dir,
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

        refs = helpers.download_message_attachments(
            _ctx(
                client, storage, "teams-chats/foo_abc", settings=_config(), converters_config={}, failed_attachments={}
            ),
            msg,
        )

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

        refs = helpers.download_message_attachments(
            _ctx(
                client, storage, "teams-chats/foo_abc", settings=_config(), converters_config={}, failed_attachments={}
            ),
            msg,
        )

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
            _ctx(
                client,
                storage,
                "teams-chats/foo_abc",
                settings=_config(max_mb=100),
                converters_config={},
                failed_attachments={},
            ),
            msg,
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

        refs = helpers.download_message_attachments(
            _ctx(
                client, storage, "teams-chats/foo_abc", settings=_config(), converters_config={}, failed_attachments={}
            ),
            msg,
        )

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

        refs = helpers.download_message_attachments(
            _ctx(
                client, storage, "teams-chats/foo_abc", settings=_config(), converters_config={}, failed_attachments={}
            ),
            msg,
        )

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

        refs = helpers.download_message_attachments(
            _ctx(
                client, storage, "teams-chats/foo_abc", settings=_config(), converters_config={}, failed_attachments={}
            ),
            msg,
        )

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
                _ctx(
                    client,
                    storage,
                    "teams-chats/foo_abc",
                    settings=_config(convert=[".pdf"]),
                    converters_config={"backends": {"pdf": "markitdown"}},
                    failed_attachments={},
                ),
                msg,
            )

        assert mock_conv.call_count == 1
        assert refs[0].converted_path == "attachments_converted/msg-7/spec.pdf.md"
        target_path = mock_conv.call_args.args[3]
        assert target_path == "teams-chats/foo_abc/attachments_converted/msg-7/spec.pdf.md"
        client.close()

    def test_failed_conversion_clears_converted_path(self, httpx_mock: HTTPXMock, tmp_path, graph_config) -> None:
        """When conversion fails, the ref must not carry a dangling converted link."""
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

        with patch.object(helpers, "convert_and_store", return_value=False):
            refs = helpers.download_message_attachments(
                _ctx(
                    client,
                    storage,
                    "teams-chats/foo_abc",
                    settings=_config(convert=[".pdf"]),
                    converters_config={"backends": {"pdf": "markitdown"}},
                    failed_attachments={},
                ),
                msg,
            )

        assert refs[0].converted_path is None
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

        refs = helpers.download_message_attachments(
            _ctx(
                client, storage, "teams-chats/foo_abc", settings=_config(), converters_config={}, failed_attachments={}
            ),
            msg,
        )

        assert len(refs) == 1
        assert refs[0].name == "escape.txt"
        files = storage.list_files("teams-chats")
        assert "teams-chats/foo_abc/attachments/msg-8/escape.txt" in files
        # Confirm nothing escaped the vault
        assert not any(".." in f for f in files)
        client.close()

    def test_transient_download_failure_logged_and_not_recorded(
        self, httpx_mock: HTTPXMock, tmp_path, graph_config
    ) -> None:
        content_url = "https://sanoptis.sharepoint.com/sites/x/spec.pdf"
        encoded = helpers._encode_share_url(content_url)
        # max_retries=1 → the client attempts twice before raising
        for _ in range(2):
            httpx_mock.add_response(
                url=re.compile(rf".*/shares/{re.escape(encoded)}/driveItem.*"),
                status_code=500,
                text='{"error":{"code":"InternalError","message":"boom"}}',
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

        failed: dict[str, str] = {}
        with patch.object(helpers.log, "warning", side_effect=capture):
            refs = helpers.download_message_attachments(
                _ctx(
                    client,
                    storage,
                    "teams-chats/foo_abc",
                    settings=_config(),
                    converters_config={},
                    failed_attachments=failed,
                ),
                msg,
            )

        assert refs == []
        download_failures = [w for w in warnings if w["event"] == "teams_attachments.attachment_download_failed"]
        assert len(download_failures) == 1
        assert failed == {}
        client.close()


class TestPermanentFailureSkipList:
    def _msg(self, content_url: str) -> dict:
        return {
            "id": "msg-denied",
            "attachments": [{"contentType": "reference", "name": "secret.pdf", "contentUrl": content_url}],
        }

    @pytest.mark.parametrize("status,code", [(403, "accessDenied"), (404, "itemNotFound")])
    def test_permanent_failure_recorded_and_logged(
        self, httpx_mock: HTTPXMock, tmp_path, graph_config, status, code
    ) -> None:
        content_url = "https://sanoptis-my.sharepoint.com/personal/other_user/secret.pdf"
        encoded = helpers._encode_share_url(content_url)
        httpx_mock.add_response(
            url=re.compile(rf".*/shares/{re.escape(encoded)}/driveItem.*"),
            status_code=status,
            text=f'{{"error":{{"code":"{code}","message":"denied"}}}}',
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")
        failed: dict[str, str] = {}

        warnings: list[dict] = []

        def capture(event, **kwargs):
            warnings.append({"event": event, **kwargs})

        with patch.object(helpers.log, "warning", side_effect=capture):
            refs = helpers.download_message_attachments(
                _ctx(
                    client,
                    storage,
                    "teams-chats/foo_abc",
                    settings=_config(),
                    converters_config={},
                    failed_attachments=failed,
                ),
                self._msg(content_url),
            )

        assert refs == []
        assert failed == {"msg-denied:secret.pdf": f"http_{status}"}
        events = [w["event"] for w in warnings]
        assert events == ["teams_attachments.attachment_download_failed_permanently"]
        client.close()

    def test_previously_failed_skipped_without_request(self, httpx_mock: HTTPXMock, tmp_path, graph_config) -> None:
        content_url = "https://sanoptis-my.sharepoint.com/personal/other_user/secret.pdf"
        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")
        failed = {"msg-denied:secret.pdf": "http_403"}

        warnings: list[dict] = []

        def capture(event, **kwargs):
            warnings.append({"event": event, **kwargs})

        with patch.object(helpers.log, "warning", side_effect=capture):
            refs = helpers.download_message_attachments(
                _ctx(
                    client,
                    storage,
                    "teams-chats/foo_abc",
                    settings=_config(),
                    converters_config={},
                    failed_attachments=failed,
                ),
                self._msg(content_url),
            )

        assert refs == []
        assert warnings == []
        assert httpx_mock.get_requests() == []
        client.close()

    def test_nonfile_reference_types_silently_skipped(self, httpx_mock: HTTPXMock, tmp_path, graph_config) -> None:
        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")
        msg = {
            "id": "msg-fwd",
            "attachments": [
                {"contentType": "forwardedMessageReference", "id": "a1"},
                {"contentType": "application/vnd.microsoft.card.adaptive", "id": "a2"},
            ],
        }

        warnings: list[dict] = []

        def capture(event, **kwargs):
            warnings.append({"event": event, **kwargs})

        with patch.object(helpers.log, "warning", side_effect=capture):
            refs = helpers.download_message_attachments(
                _ctx(
                    client,
                    storage,
                    "teams-chats/foo_abc",
                    settings=_config(),
                    converters_config={},
                    failed_attachments={},
                ),
                msg,
            )

        assert refs == []
        assert warnings == []
        assert httpx_mock.get_requests() == []
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

        hosted_map = hosted_content.download_inline_images(
            _ctx(client, storage, "teams-chats/foo_abc", settings=_config()), f"/chats/{chat_id}/messages/{msg_id}", msg
        )

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

        hosted_map = hosted_content.download_inline_images(
            _ctx(client, storage, "teams-chats/foo_abc", settings=_config()), f"/chats/{chat_id}/messages/{msg_id}", msg
        )

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

        hosted_map = hosted_content.download_inline_images(
            _ctx(client, storage, "teams-chats/foo_abc", settings=_config(max_mb=1)),
            f"/chats/{chat_id}/messages/{msg_id}",
            msg,
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

        hosted_map = hosted_content.download_inline_images(
            _ctx(client, storage, "teams-chats/foo_abc", settings=_config()),
            f"/chats/{chat_id}/messages/{msg_id}",
            {"id": msg_id},
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

        hosted_map = hosted_content.download_inline_images(
            _ctx(mock_client, storage, "teams-chats/foo_abc", settings=_config()),
            f"/chats/{chat_id}/messages/{msg_id}",
            {"id": msg_id},
        )

        assert hosted_map == {}

    def test_no_msg_id_returns_empty(self, tmp_path, graph_config) -> None:
        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")
        assert (
            hosted_content.download_inline_images(
                _ctx(client, storage, "teams-chats/foo_abc", settings=_config()), "/chats/19:abc/messages/x", {}
            )
            == {}
        )
        client.close()

    def test_empty_hosted_content_id_skipped(self, tmp_path, graph_config) -> None:
        from unittest.mock import MagicMock

        storage = LocalBackend(str(tmp_path / "vault"))
        mock_client = MagicMock(spec=GraphClient)
        mock_client.max_pages = 5
        mock_client.get_paginated.return_value = iter([{"id": ""}, {}, {"id": "HID-VALID"}])
        mock_client.get_bytes_with_content_type.return_value = (b"\x89PNG\r\n\x1a\n", "image/png")

        hosted_map = hosted_content.download_inline_images(
            _ctx(mock_client, storage, "teams-chats/foo_abc", settings=_config()),
            "/chats/19:pqr/messages/6",
            {"id": "6"},
        )

        assert "HID-VALID" in hosted_map
        assert len(hosted_map) == 1
        mock_client.get_bytes_with_content_type.assert_called_once()

    def test_storage_error_on_write_skips_item(self, tmp_path, graph_config) -> None:
        from unittest.mock import MagicMock

        from m365_brain.storage.exceptions import StorageError

        mock_storage = MagicMock(spec=LocalBackend)
        mock_storage.write_bytes.side_effect = StorageError("disk full")
        mock_client = MagicMock(spec=GraphClient)
        mock_client.max_pages = 5
        mock_client.get_paginated.return_value = iter([{"id": "HID-ERR"}])
        mock_client.get_bytes_with_content_type.return_value = (b"\x89PNG\r\n\x1a\n", "image/png")

        hosted_map = hosted_content.download_inline_images(
            _ctx(mock_client, mock_storage, "teams-chats/foo_abc", settings=_config()),
            "/chats/19:stu/messages/7",
            {"id": "7"},
        )

        assert hosted_map == {}
        mock_storage.write_bytes.assert_called_once()

    def test_os_error_on_write_skips_item(self, tmp_path, graph_config) -> None:
        from unittest.mock import MagicMock

        mock_storage = MagicMock(spec=LocalBackend)
        mock_storage.write_bytes.side_effect = OSError("permission denied")
        mock_client = MagicMock(spec=GraphClient)
        mock_client.max_pages = 5
        mock_client.get_paginated.return_value = iter([{"id": "HID-OS"}])
        mock_client.get_bytes_with_content_type.return_value = (b"\x89PNG\r\n\x1a\n", "image/png")

        hosted_map = hosted_content.download_inline_images(
            _ctx(mock_client, mock_storage, "teams-chats/foo_abc", settings=_config()),
            "/chats/19:vwx/messages/8",
            {"id": "8"},
        )

        assert hosted_map == {}
        mock_storage.write_bytes.assert_called_once()


class TestSkipsEmpty:
    def test_no_attachments_returns_empty(self, tmp_path, graph_config) -> None:
        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")
        assert (
            helpers.download_message_attachments(
                _ctx(
                    client,
                    storage,
                    "teams-chats/foo_abc",
                    settings=_config(),
                    converters_config={},
                    failed_attachments={},
                ),
                {"id": "x"},
            )
            == []
        )
        client.close()

    def test_no_msg_id_returns_empty(self, tmp_path, graph_config) -> None:
        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")
        msg = {"attachments": [{"contentType": "reference", "name": "x", "contentUrl": "https://y"}]}
        assert (
            helpers.download_message_attachments(
                _ctx(
                    client,
                    storage,
                    "teams-chats/foo_abc",
                    settings=_config(),
                    converters_config={},
                    failed_attachments={},
                ),
                msg,
            )
            == []
        )
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
        client.get_bytes.side_effect = GraphApiError("Download URL blocked: ...", None)

        msg = {
            "id": "msg-evil",
            "attachments": [
                {"contentType": "reference", "name": "x.pdf", "contentUrl": "https://sanoptis.sharepoint.com/x"}
            ],
        }

        refs = helpers.download_message_attachments(
            _ctx(
                client, storage, "teams-chats/foo_abc", settings=_config(), converters_config={}, failed_attachments={}
            ),
            msg,
        )
        assert refs == []


class TestIsDownloadable:
    def test_reference_with_name_and_url(self) -> None:
        att = {"contentType": "reference", "name": "spec.pdf", "contentUrl": "https://example.com/spec.pdf"}
        assert helpers._is_downloadable(att, "msg-1", {}) is True

    def test_non_reference_type(self) -> None:
        att = {"contentType": "messageReference", "name": "n", "contentUrl": "https://x"}
        assert helpers._is_downloadable(att, "msg-1", {}) is False

    def test_missing_name(self) -> None:
        att = {"contentType": "reference", "name": "", "contentUrl": "https://x"}
        assert helpers._is_downloadable(att, "msg-1", {}) is False

    def test_missing_content_url(self) -> None:
        att = {"contentType": "reference", "name": "spec.pdf", "contentUrl": ""}
        assert helpers._is_downloadable(att, "msg-1", {}) is False

    def test_previously_failed(self) -> None:
        att = {"contentType": "reference", "name": "spec.pdf", "contentUrl": "https://x"}
        assert helpers._is_downloadable(att, "msg-1", {"msg-1:spec.pdf": "http_403"}) is False

    def test_path_traversal_name_sanitized(self) -> None:
        att = {"contentType": "reference", "name": "../../escape.txt", "contentUrl": "https://x"}
        assert helpers._is_downloadable(att, "msg-1", {}) is True

    def test_none_content_type_treated_as_non_reference(self) -> None:
        att = {"name": "spec.pdf", "contentUrl": "https://x"}
        assert helpers._is_downloadable(att, "msg-1", {}) is False


class TestResolveAttachment:
    def test_skipped_content_type_returns_none(self, tmp_path, graph_config) -> None:
        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")
        att = {"contentType": "messageReference", "name": "n", "contentUrl": "https://x"}
        result = helpers._resolve_attachment(
            _ctx(client, storage, "teams-chats/foo", settings=_config(), converters_config={}, failed_attachments={}),
            att,
            "msg-1",
            100 * 1024 * 1024,
        )
        assert result is None
        client.close()

    def test_missing_fields_returns_none(self, tmp_path, graph_config) -> None:
        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")
        att = {"contentType": "reference", "name": "", "contentUrl": "https://x"}
        result = helpers._resolve_attachment(
            _ctx(client, storage, "teams-chats/foo", settings=_config(), converters_config={}, failed_attachments={}),
            att,
            "msg-1",
            100 * 1024 * 1024,
        )
        assert result is None
        client.close()

    def test_unsupported_type_returns_none(self, tmp_path, graph_config) -> None:
        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")
        att = {
            "contentType": "application/vnd.microsoft.card.codesnippet",
            "name": "snippet",
            "contentUrl": "https://x",
        }
        result = helpers._resolve_attachment(
            _ctx(client, storage, "teams-chats/foo", settings=_config(), converters_config={}, failed_attachments={}),
            att,
            "msg-1",
            100 * 1024 * 1024,
        )
        assert result is None
        client.close()

    def test_previously_failed_returns_none(self, tmp_path, graph_config) -> None:
        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")
        att = {"contentType": "reference", "name": "spec.pdf", "contentUrl": "https://x"}
        failed = {"msg-1:spec.pdf": "http_403"}
        result = helpers._resolve_attachment(
            _ctx(
                client, storage, "teams-chats/foo", settings=_config(), converters_config={}, failed_attachments=failed
            ),
            att,
            "msg-1",
            100 * 1024 * 1024,
        )
        assert result is None
        client.close()

    def test_successful_download_returns_ref(self, httpx_mock: HTTPXMock, tmp_path, graph_config) -> None:
        content_url = "https://sanoptis.sharepoint.com/sites/x/spec.pdf"
        encoded = helpers._encode_share_url(content_url)
        httpx_mock.add_response(
            url=re.compile(rf".*/shares/{re.escape(encoded)}/driveItem.*"),
            json={"id": "di", "size": 64, "@microsoft.graph.downloadUrl": "https://sanoptis.sharepoint.com/dl?t=x"},
        )
        httpx_mock.add_response(url=re.compile(r"https://sanoptis\.sharepoint\.com/dl.*"), content=b"%PDF fake")

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")
        att = {"contentType": "reference", "name": "spec.pdf", "contentUrl": content_url}
        result = helpers._resolve_attachment(
            _ctx(client, storage, "teams-chats/foo", settings=_config(), converters_config={}, failed_attachments={}),
            att,
            "msg-1",
            100 * 1024 * 1024,
        )
        assert result is not None
        assert result.name == "spec.pdf"
        assert result.relative_path == "attachments/msg-1/spec.pdf"
        assert result.converted_path is None
        client.close()

    def test_permanent_failure_updates_skip_list(self, httpx_mock: HTTPXMock, tmp_path, graph_config) -> None:
        content_url = "https://sanoptis.sharepoint.com/sites/x/secret.pdf"
        encoded = helpers._encode_share_url(content_url)
        httpx_mock.add_response(
            url=re.compile(rf".*/shares/{re.escape(encoded)}/driveItem.*"),
            status_code=403,
            text='{"error":{"code":"accessDenied","message":"denied"}}',
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")
        att = {"contentType": "reference", "name": "secret.pdf", "contentUrl": content_url}
        failed: dict[str, str] = {}
        result = helpers._resolve_attachment(
            _ctx(
                client, storage, "teams-chats/foo", settings=_config(), converters_config={}, failed_attachments=failed
            ),
            att,
            "msg-1",
            100 * 1024 * 1024,
        )
        assert result is None
        assert failed == {"msg-1:secret.pdf": "http_403"}
        client.close()

    def test_transport_error_returns_none(self, tmp_path, graph_config) -> None:
        from unittest.mock import MagicMock

        storage = LocalBackend(str(tmp_path / "vault"))
        client = MagicMock(spec=GraphClient)
        client.get.side_effect = httpx.ConnectError("network down")
        att = {"contentType": "reference", "name": "spec.pdf", "contentUrl": "https://sanoptis.sharepoint.com/x"}
        failed: dict[str, str] = {}
        result = helpers._resolve_attachment(
            _ctx(
                client, storage, "teams-chats/foo", settings=_config(), converters_config={}, failed_attachments=failed
            ),
            att,
            "msg-1",
            100 * 1024 * 1024,
        )
        assert result is None
        assert failed == {}


class TestEncodeShareUrlProperty:
    @given(url=st.text(min_size=1))
    def test_always_starts_with_u_bang(self, url: str) -> None:
        assert helpers._encode_share_url(url).startswith("u!")

    @given(url=st.text(min_size=1))
    def test_never_contains_base64_padding(self, url: str) -> None:
        """The /shares/{id} route rejects '=' padding, so it must be stripped."""
        assert "=" not in helpers._encode_share_url(url)


class TestSanitizeFilenameProperty:
    @given(name=st.text(min_size=1))
    def test_never_contains_path_separator(self, name: str) -> None:
        """Attachment names are attacker-influenced — they must not escape the directory."""
        result = helpers._sanitize_filename(name)
        assert os.sep not in result
        assert "/" not in result
