"""Tests for email attachment download and markdown conversion."""

from __future__ import annotations

import base64
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from m365_brain.config import EmailExtractorConfig, MailboxConfig
from m365_brain.m365.client import GraphApiError, GraphClient
from m365_brain.m365.converters.document import DocumentConversionError
from m365_brain.m365.extractors import _attachment_helpers as helpers
from m365_brain.m365.extractors._attachment_helpers import convert_and_store, download_attachments
from m365_brain.m365.extractors.base import ExtractorContext
from m365_brain.storage.local import LocalBackend

ITEM = "2026-03-05_subject_abc123"


def _item(ctx: ExtractorContext) -> str:
    return ctx.paths.inbox_item("email", ITEM)


def _config(*, convert: list[str], max_mb: int) -> EmailExtractorConfig:
    return EmailExtractorConfig(
        enabled=True,
        poll_interval_minutes=3,
        mailboxes=[MailboxConfig(address="me", folders=["Inbox"], output_subdir="")],
        lookback_days=30,
        max_items_per_sync=100,
        download_attachments=True,
        max_attachment_size_mb=max_mb,
        attachment_convert_extensions=convert,
    )


def _client(attachments: list[dict]) -> MagicMock:
    client = MagicMock(spec=GraphClient)
    client.get_paginated.return_value = iter(attachments)
    return client


def _download(client: MagicMock, storage: LocalBackend, config: EmailExtractorConfig, ctx: ExtractorContext) -> None:
    download_attachments(client, storage, "/me", "msg-1", ctx.paths.inbox_item("email", ITEM), config, ctx)


class TestDownloadAttachments:
    def test_inline_base64_content_is_decoded_and_written(
        self, local_storage: LocalBackend, ctx: ExtractorContext
    ) -> None:
        client = _client([{"name": "notes.txt", "size": 11, "contentBytes": base64.b64encode(b"hello world").decode()}])
        _download(client, local_storage, _config(convert=[], max_mb=25), ctx)

        assert local_storage.read_file(ctx.paths.attachment(_item(ctx), "notes.txt")) == "hello world"
        client.get_bytes.assert_not_called()

    def test_download_url_takes_precedence_over_content_bytes(
        self, local_storage: LocalBackend, ctx: ExtractorContext
    ) -> None:
        client = _client(
            [
                {
                    "name": "spec.pdf",
                    "size": 12,
                    "@microsoft.graph.downloadUrl": "https://tenant.sharepoint.com/dl?t=x",
                    "contentBytes": base64.b64encode(b"stale bytes").decode(),
                }
            ]
        )
        client.get_bytes.return_value = b"%PDF-1.4 fresh"
        _download(client, local_storage, _config(convert=[], max_mb=25), ctx)

        client.get_bytes.assert_called_once_with("https://tenant.sharepoint.com/dl?t=x")
        assert local_storage.read_file(ctx.paths.attachment(_item(ctx), "spec.pdf")) == "%PDF-1.4 fresh"

    def test_inline_oversized_unnamed_and_payloadless_are_all_skipped(
        self, local_storage: LocalBackend, ctx: ExtractorContext
    ) -> None:
        client = _client(
            [
                {"name": "logo.png", "size": 10, "isInline": True, "contentBytes": base64.b64encode(b"x").decode()},
                {"name": "huge.zip", "size": 30 * 1024 * 1024, "contentBytes": base64.b64encode(b"x").decode()},
                {"name": "", "contentBytes": base64.b64encode(b"x").decode()},
                {"name": "C:evil.txt", "contentBytes": base64.b64encode(b"x").decode()},
                {"name": "empty.txt", "size": 1},
            ]
        )
        _download(client, local_storage, _config(convert=[], max_mb=25), ctx)

        assert local_storage.list_files(ctx.paths.inbox_root("email")) == []
        client.get_bytes.assert_not_called()

    def test_directory_traversal_in_name_is_flattened(self, local_storage: LocalBackend, ctx: ExtractorContext) -> None:
        client = _client(
            [{"name": "../../../etc/passwd", "size": 4, "contentBytes": base64.b64encode(b"root").decode()}]
        )
        _download(client, local_storage, _config(convert=[], max_mb=25), ctx)

        files = local_storage.list_files(ctx.paths.inbox_root("email"))
        assert files == [ctx.paths.attachment(_item(ctx), "passwd")]
        assert not any(".." in f for f in files)

    @pytest.mark.parametrize(
        "name",
        [r"C:\Users\x\report.pdf", "/tmp/x/report.pdf", r"..\..\report.pdf"],
        ids=["windows-absolute", "posix-absolute", "windows-traversal"],
    )
    def test_a_path_shaped_name_is_stored_under_its_basename(
        self, local_storage: LocalBackend, ctx: ExtractorContext, name: str
    ) -> None:
        """The basename strip is what makes the name safe, so it has to run first.

        Rejecting on the colon before stripping discarded every Windows-shaped
        name outright -- a dropped attachment reported as a clean sync.
        """
        client = _client([{"name": name, "size": 4, "contentBytes": base64.b64encode(b"data").decode()}])
        _download(client, local_storage, _config(convert=[], max_mb=25), ctx)

        assert local_storage.list_files(ctx.paths.inbox_root("email")) == [
            ctx.paths.attachment(_item(ctx), "report.pdf")
        ]

    @pytest.mark.parametrize(
        "name",
        ["", "/", ".", r"C:evil.txt"],
        ids=["empty", "root", "current-dir", "drive-relative"],
    )
    def test_a_name_with_no_usable_basename_is_skipped_loudly(
        self, local_storage: LocalBackend, ctx: ExtractorContext, name: str
    ) -> None:
        client = _client([{"name": name, "size": 4, "contentBytes": base64.b64encode(b"data").decode()}])

        with patch.object(helpers.log, "warning") as mock_warning:
            _download(client, local_storage, _config(convert=[], max_mb=25), ctx)

        assert local_storage.list_files(ctx.paths.inbox_root("email")) == []
        assert mock_warning.call_args.args == ("email.attachment_unusable_name",)
        assert mock_warning.call_args.kwargs["name"] == name

    def test_matching_extension_is_queued_for_conversion(
        self, local_storage: LocalBackend, ctx: ExtractorContext
    ) -> None:
        client = _client([{"name": "spec.pdf", "size": 5, "contentBytes": base64.b64encode(b"%PDF-").decode()}])

        with patch.object(helpers, "convert_and_store") as mock_convert:
            _download(client, local_storage, _config(convert=[".pdf"], max_mb=25), ctx)

        assert mock_convert.call_count == 1
        storage_arg, data, source_name, target_path, converters_config = mock_convert.call_args.args
        assert storage_arg is local_storage
        assert data == b"%PDF-"
        assert source_name == "spec.pdf"
        assert target_path == ctx.paths.converted_attachment(_item(ctx), "spec.pdf.md")
        assert converters_config == ctx.converters

    def test_one_failed_download_does_not_abort_the_rest(
        self, local_storage: LocalBackend, ctx: ExtractorContext
    ) -> None:
        client = _client(
            [
                {"name": "broken.pdf", "size": 5, "@microsoft.graph.downloadUrl": "https://tenant.sharepoint.com/a"},
                {"name": "ok.txt", "size": 2, "contentBytes": base64.b64encode(b"ok").decode()},
            ]
        )
        client.get_bytes.side_effect = GraphApiError("Download URL blocked", None)

        _download(client, local_storage, _config(convert=[], max_mb=25), ctx)

        assert local_storage.list_files(ctx.paths.inbox_root("email")) == [ctx.paths.attachment(_item(ctx), "ok.txt")]

    def test_listing_failure_is_swallowed_not_propagated(
        self, local_storage: LocalBackend, ctx: ExtractorContext
    ) -> None:
        """A failed attachment listing must not abort the message's markdown write."""
        client = MagicMock(spec=GraphClient)
        client.get_paginated.side_effect = GraphApiError("HTTP 503", 503)

        _download(client, local_storage, _config(convert=[], max_mb=25), ctx)

        assert local_storage.list_files(ctx.paths.inbox_root("email")) == []


class TestConvertAndStore:
    def test_writes_markdown_and_removes_the_temp_file(
        self, local_storage: LocalBackend, ctx: ExtractorContext
    ) -> None:
        seen: list[Path] = []

        def fake_convert(path: Path, converters_config: dict) -> str:
            seen.append(path)
            assert path.exists()
            assert path.read_bytes() == b"%PDF-1.4"
            assert path.suffix == ".pdf"
            return "# Converted"

        with patch.object(helpers, "convert_document", side_effect=fake_convert):
            written = convert_and_store(local_storage, b"%PDF-1.4", "spec.pdf", f"{_item(ctx)}/out.md", {})

        assert written is True
        assert local_storage.read_file(f"{_item(ctx)}/out.md") == "# Converted"
        assert not seen[0].exists()

    @pytest.mark.parametrize("error", [DocumentConversionError("bad pdf"), ImportError("no backend"), OSError("io")])
    def test_conversion_failure_writes_nothing_and_reports_false(
        self, local_storage: LocalBackend, ctx: ExtractorContext, error: Exception
    ) -> None:
        with patch.object(helpers, "convert_document", side_effect=error):
            written = convert_and_store(local_storage, b"data", "spec.pdf", f"{_item(ctx)}/out.md", {})

        assert written is False
        assert local_storage.list_files(ctx.paths.inbox_root("email")) == []
