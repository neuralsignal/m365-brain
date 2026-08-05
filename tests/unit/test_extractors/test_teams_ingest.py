"""Tests for the shared Teams ingest helpers (``_teams_ingest``)."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from hypothesis import given
from hypothesis import strategies as st

from m365_brain.config import TeamsChatsExtractorConfig
from m365_brain.extractors._message_store import StoredMessage
from m365_brain.extractors._teams_attachment_helpers import (
    download_message_attachments,
    downloadable_attachment_names,
)
from m365_brain.extractors._teams_context import TeamsContext
from m365_brain.extractors._teams_ingest import GRAPH_PAGE_SIZE, is_etag_fresh, to_stored_message
from m365_brain.storage.local import LocalBackend


def _settings(*, download_attachments: bool, download_inline_images: bool) -> TeamsChatsExtractorConfig:
    return TeamsChatsExtractorConfig(
        enabled=True,
        poll_interval_minutes=5,
        max_messages_per_chat=200,
        download_attachments=download_attachments,
        download_inline_images=download_inline_images,
        max_attachment_size_mb=25,
        attachment_convert_extensions=[],
    )


def _stored(msg_id: str, *, etag: str, content: str, attachments: list[dict], deleted: bool) -> StoredMessage:
    return StoredMessage(
        id=msg_id,
        parent_id=None,
        sender="Alice",
        created="2026-06-10T09:00:00Z",
        last_modified="2026-06-10T09:00:00Z",
        etag=etag,
        edited=False,
        deleted=deleted,
        content=content,
        attachments=attachments,
        subject=None,
    )


def _graph_msg(msg_id: str, *, etag: str, attachments: list[dict]) -> dict:
    return {
        "id": msg_id,
        "messageType": "message",
        "createdDateTime": "2026-06-10T09:00:00Z",
        "lastModifiedDateTime": "2026-06-12T08:00:00Z",
        "etag": etag,
        "lastEditedDateTime": None,
        "deletedDateTime": None,
        "from": {"user": {"displayName": "Alice", "id": "u1"}},
        "body": {"contentType": "text", "content": "hello"},
        "attachments": attachments,
    }


def _ref_attachment(name: str) -> dict:
    return {"contentType": "reference", "name": name, "contentUrl": f"https://sanoptis.sharepoint.com/x/{name}"}


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


class TestIsEtagFresh:
    def test_missing_prior_is_not_fresh(self):
        assert is_etag_fresh(None, {"etag": "1"}) is False

    def test_same_etag_is_fresh(self):
        prior = _stored("m1", etag="1", content="hi", attachments=[], deleted=False)
        assert is_etag_fresh(prior, {"etag": "1"}) is True

    def test_different_etag_is_not_fresh(self):
        prior = _stored("m1", etag="1", content="hi", attachments=[], deleted=False)
        assert is_etag_fresh(prior, {"etag": "2"}) is False


class TestDownloadableAttachmentNames:
    def test_filters_non_reference_and_incomplete_entries(self):
        msg = _graph_msg(
            "m1",
            etag="1",
            attachments=[
                _ref_attachment("spec.pdf"),
                {"contentType": "application/vnd.microsoft.card.adaptive", "name": "card", "contentUrl": "https://x"},
                {"contentType": "reference", "name": "", "contentUrl": "https://x"},
                {"contentType": "reference", "name": "no-url.pdf", "contentUrl": ""},
            ],
        )
        assert downloadable_attachment_names(msg, {}) == {"spec.pdf"}

    def test_excludes_permanently_failed_attachments(self):
        msg = _graph_msg("m1", etag="1", attachments=[_ref_attachment("spec.pdf"), _ref_attachment("gone.pdf")])
        assert downloadable_attachment_names(msg, {"m1:gone.pdf": "http_404"}) == {"spec.pdf"}

    def test_sanitizes_directory_components(self):
        msg = _graph_msg(
            "m1",
            etag="1",
            attachments=[{"contentType": "reference", "name": "../../evil.pdf", "contentUrl": "https://x"}],
        )
        assert downloadable_attachment_names(msg, {}) == {"evil.pdf"}

    @given(names=st.lists(st.text(min_size=1, max_size=20), max_size=5))
    def test_result_names_derive_from_sanitized_payload_names(self, names: list[str]):
        msg = _graph_msg("m1", etag="1", attachments=[_ref_attachment(n) for n in names])
        result = downloadable_attachment_names(msg, {})
        sanitized = {Path(n).name for n in names} - {""}
        assert result <= sanitized

    def test_matches_download_message_attachments_result(self, tmp_path):
        """Equivalence pin: the predicate must mirror what the downloader actually attempts."""
        msg = _graph_msg(
            "m1",
            etag="1",
            attachments=[
                _ref_attachment("spec.pdf"),
                _ref_attachment("other.docx"),
                {"contentType": "messageReference", "name": "quoted", "contentUrl": "https://x"},
            ],
        )
        client = MagicMock()
        client.get.return_value = {"size": 8, "@microsoft.graph.downloadUrl": "https://dl"}
        client.get_bytes.return_value = b"data"
        storage = LocalBackend(str(tmp_path / "vault"))
        settings = _settings(download_attachments=True, download_inline_images=False)

        refs = download_message_attachments(
            _ctx(client, storage, "conv", settings=settings, converters_config={}, failed_attachments={}), msg
        )

        assert {r.name for r in refs} == downloadable_attachment_names(msg, {})


class TestToStoredMessageReuse:
    def _convert(self, msg: dict, prior: StoredMessage | None, client) -> StoredMessage:
        settings = _settings(download_attachments=True, download_inline_images=True)
        storage = MagicMock()
        return to_stored_message(
            _ctx(client, storage, "conv", settings=settings, converters_config={}, failed_attachments={}),
            msg,
            None,
            "/chats/c1/messages/m1",
            prior,
        )

    def test_reaction_only_bump_reuses_prior_media_without_client_calls(self):
        prior_refs = [{"name": "spec.pdf", "relative_path": "attachments/m1/spec.pdf", "converted_path": None}]
        prior = _stored("m1", etag="1", content="hello with local image", attachments=prior_refs, deleted=False)
        msg = _graph_msg("m1", etag="2", attachments=[_ref_attachment("spec.pdf")])
        client = MagicMock()
        client.get.side_effect = AssertionError("no Graph calls expected on media reuse")
        client.get_paginated.side_effect = AssertionError("no Graph calls expected on media reuse")
        client.get_pages.side_effect = AssertionError("no Graph calls expected on media reuse")

        result = self._convert(msg, prior, client)

        assert result.etag == "2"
        assert result.last_modified == "2026-06-12T08:00:00Z"
        assert result.content == "hello with local image"
        assert result.attachments == prior_refs

    def test_edited_message_does_not_reuse_prior_content(self):
        prior = _stored("m1", etag="1", content="old body", attachments=[], deleted=False)
        msg = _graph_msg("m1", etag="2", attachments=[])
        msg["lastEditedDateTime"] = "2026-06-12T08:00:00Z"
        msg["body"] = {"contentType": "text", "content": "new body"}
        client = MagicMock()
        client.get_paginated.return_value = iter([])

        result = self._convert(msg, prior, client)

        assert result.content == "new body"
        assert result.edited is True

    def test_tombstone_does_not_reuse_prior_media(self):
        prior_refs = [{"name": "spec.pdf", "relative_path": "attachments/m1/spec.pdf", "converted_path": None}]
        prior = _stored("m1", etag="1", content="hello", attachments=prior_refs, deleted=False)
        msg = _graph_msg("m1", etag="2", attachments=[])
        msg["deletedDateTime"] = "2026-06-12T08:00:00Z"
        msg["body"] = {"contentType": "text", "content": ""}
        client = MagicMock()
        client.get_paginated.return_value = iter([])

        result = self._convert(msg, prior, client)

        assert result.deleted is True
        assert result.attachments == []

    def test_changed_attachment_set_does_not_reuse(self):
        prior_refs = [{"name": "spec.pdf", "relative_path": "attachments/m1/spec.pdf", "converted_path": None}]
        prior = _stored("m1", etag="1", content="hello", attachments=prior_refs, deleted=False)
        msg = _graph_msg("m1", etag="2", attachments=[_ref_attachment("spec.pdf"), _ref_attachment("extra.pdf")])
        client = MagicMock()
        client.get.return_value = {"size": 8, "@microsoft.graph.downloadUrl": "https://dl"}
        client.get_bytes.return_value = b"data"
        client.get_paginated.return_value = iter([])
        settings = _settings(download_attachments=True, download_inline_images=False)
        storage = MagicMock()

        result = to_stored_message(
            _ctx(client, storage, "conv", settings=settings, converters_config={}, failed_attachments={}),
            msg,
            None,
            "/chats/c1/messages/m1",
            prior,
        )

        assert sorted(a["name"] for a in result.attachments) == ["extra.pdf", "spec.pdf"]


class TestGraphPageSize:
    def test_is_the_documented_teams_top_maximum(self):
        assert GRAPH_PAGE_SIZE == 50


class TestTeamsContextImmutable:
    """Callees receive the context by reference; the refactor relies on immutability."""

    def test_is_frozen(self):
        ctx = _ctx(
            MagicMock(),
            MagicMock(),
            "conv",
            settings=_settings(download_attachments=False, download_inline_images=False),
        )
        with pytest.raises(FrozenInstanceError):
            ctx.conv_dir = "elsewhere"  # type: ignore[misc]

    def test_failed_attachments_is_shared_not_copied(self):
        """The skip-list must be the caller's dict — writes by callees have to persist."""
        skip_list: dict[str, str] = {}
        ctx = _ctx(
            MagicMock(),
            MagicMock(),
            "conv",
            settings=_settings(download_attachments=False, download_inline_images=False),
            failed_attachments=skip_list,
        )
        ctx.failed_attachments["msg-1:doc.pdf"] = "http_403"
        assert skip_list == {"msg-1:doc.pdf": "http_403"}
