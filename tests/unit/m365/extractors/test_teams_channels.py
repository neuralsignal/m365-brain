"""Tests for the merge-based Teams channel extractor (Teams Sync v2)."""

from __future__ import annotations

import re
from dataclasses import FrozenInstanceError
from unittest.mock import patch
from urllib.parse import unquote_plus

import httpx
import pytest
from pytest_httpx import HTTPXMock

from m365_brain.config import ExplicitChannel, GraphConfig, TeamsChannelsExtractorConfig
from m365_brain.m365.client import GRAPH_BASE_URL, GraphClient
from m365_brain.m365.extractors import teams_channels
from m365_brain.m365.extractors._message_store import load_store
from m365_brain.m365.markdown_writer import short_hash, slugify
from m365_brain.storage.local import LocalBackend
from m365_brain.vault.paths import VaultPaths
from m365_brain.vault.removal import PATH_MAP_STATE_KEY

TEAM_ID = "team-1"
CHANNEL_ID = "ch-1"
WATERMARK_KEY = f"{TEAM_ID}:{CHANNEL_ID}"
MESSAGES_URL = re.compile(rf".*/teams/{TEAM_ID}/channels/{CHANNEL_ID}/messages\?.*")


def _conv_dir(paths: VaultPaths) -> str:
    """Where the mocked Engineering/General channel lands under a given layout."""
    return paths.inbox_item("teams_channels", "engineering", f"general-{short_hash(CHANNEL_ID, 6)}")


def _config(
    max_messages: int = 200,
    channels: list[ExplicitChannel] | None = None,
) -> TeamsChannelsExtractorConfig:
    return TeamsChannelsExtractorConfig(
        enabled=True,
        poll_interval_minutes=5,
        max_messages_per_channel=max_messages,
        download_attachments=False,
        download_inline_images=False,
        max_attachment_size_mb=25,
        attachment_convert_extensions=[],
        channels=channels,
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
def storage(tmp_path):
    return LocalBackend(str(tmp_path / "vault"))


@pytest.fixture()
def client(graph_config):
    c = GraphClient(graph_config, lambda: "test-token")
    yield c
    c.close()


@pytest.fixture()
def conv_dir(ctx) -> str:
    return _conv_dir(ctx.paths)


@pytest.fixture()
def md_path(ctx, conv_dir) -> str:
    return ctx.paths.conversation_file(conv_dir)


@pytest.fixture()
def store_path(ctx, conv_dir) -> str:
    return ctx.paths.conversation_store(conv_dir)


def _graph_msg(
    msg_id: str,
    created: str,
    *,
    content: str = "hello",
    etag: str = "1",
    last_modified: str | None = None,
    msg_type: str = "message",
    sender: str = "Alice",
    subject: str | None = None,
) -> dict:
    return {
        "id": msg_id,
        "messageType": msg_type,
        "createdDateTime": created,
        "lastModifiedDateTime": last_modified if last_modified else created,
        "etag": etag,
        "lastEditedDateTime": None,
        "deletedDateTime": None,
        "subject": subject,
        "from": {"user": {"displayName": sender, "id": "u1"}},
        "body": {"contentType": "text", "content": content},
    }


def _mock_team_and_channel(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=re.compile(r".*/me/joinedTeams.*"),
        json={"value": [{"id": TEAM_ID, "displayName": "Engineering"}]},
    )
    httpx_mock.add_response(
        url=re.compile(rf".*/teams/{TEAM_ID}/channels\?.*"),
        json={"value": [{"id": CHANNEL_ID, "displayName": "General"}]},
    )


class TestChannelSync:
    def test_threaded_markdown_with_replies_under_root(
        self, httpx_mock: HTTPXMock, storage, client, ctx, md_path, store_path
    ):
        root = _graph_msg("root-1", "2026-06-11T09:00:00Z", content="root message", subject="Planning")
        root["replies"] = [
            _graph_msg("r-1", "2026-06-11T10:00:00Z", content="first reply", sender="Bob"),
        ]
        _mock_team_and_channel(httpx_mock)
        httpx_mock.add_response(url=MESSAGES_URL, json={"value": [root]})

        state, count = teams_channels.run(client, storage, {}, _config(), ctx)

        assert count == 1
        content = storage.read_file(md_path)
        assert "# Engineering / General" in content
        assert "### 09:00 — Alice — Planning" in content
        assert "#### ↳ 10:00 — Bob" in content
        assert content.index("root message") < content.index("first reply")
        assert len(load_store(storage, store_path)) == 2

    def test_request_shape_expands_replies(self, httpx_mock: HTTPXMock, storage, client, ctx):
        _mock_team_and_channel(httpx_mock)
        httpx_mock.add_response(url=MESSAGES_URL, json={"value": []})

        teams_channels.run(client, storage, {}, _config(), ctx)

        msg_request = [r for r in httpx_mock.get_requests() if f"/channels/{CHANNEL_ID}/messages" in str(r.url)][0]
        url = unquote_plus(str(msg_request.url))
        assert "$expand=replies" in url
        assert "$top=50" in url
        assert "/delta" not in url

    def test_replies_next_link_followed(self, httpx_mock: HTTPXMock, storage, client, ctx, md_path):
        replies_next = f"{GRAPH_BASE_URL}/teams/{TEAM_ID}/channels/{CHANNEL_ID}/messages/root-1/replies?$skip=1"
        root = _graph_msg("root-1", "2026-06-11T09:00:00Z", content="root message")
        root["replies"] = [_graph_msg("r-1", "2026-06-11T10:00:00Z", content="inline reply")]
        root["replies@odata.nextLink"] = replies_next
        _mock_team_and_channel(httpx_mock)
        httpx_mock.add_response(url=MESSAGES_URL, json={"value": [root]})
        httpx_mock.add_response(
            url=replies_next,
            json={"value": [_graph_msg("r-2", "2026-06-11T11:00:00Z", content="paginated reply")]},
        )

        teams_channels.run(client, storage, {}, _config(), ctx)

        content = storage.read_file(md_path)
        assert "inline reply" in content
        assert "paginated reply" in content

    def test_watermark_keyed_by_team_and_channel(self, httpx_mock: HTTPXMock, storage, client, ctx):
        root = _graph_msg("root-1", "2026-06-11T09:00:00Z")
        root["replies"] = [_graph_msg("r-1", "2026-06-11T10:00:00Z", last_modified="2026-06-11T12:00:00Z")]
        _mock_team_and_channel(httpx_mock)
        httpx_mock.add_response(url=MESSAGES_URL, json={"value": [root]})

        state, _ = teams_channels.run(client, storage, {}, _config(), ctx)

        assert state["watermarks"][WATERMARK_KEY] == "2026-06-11T12:00:00Z"

    def test_path_map_records_the_written_markdown(self, httpx_mock: HTTPXMock, storage, client, ctx, md_path):
        """No upstream removal signal exists for channels, but `vault purge` still needs the map."""
        root = _graph_msg("root-1", "2026-06-11T09:00:00Z")
        root["replies"] = []
        _mock_team_and_channel(httpx_mock)
        httpx_mock.add_response(url=MESSAGES_URL, json={"value": [root]})

        state, _ = teams_channels.run(client, storage, {}, _config(), ctx)

        assert state[PATH_MAP_STATE_KEY] == {WATERMARK_KEY: md_path}

    def test_non_message_types_skipped(self, httpx_mock: HTTPXMock, storage, client, ctx, md_path):
        root = _graph_msg("root-1", "2026-06-11T09:00:00Z", content="real root")
        root["replies"] = [
            _graph_msg("r-sys", "2026-06-11T09:30:00Z", content="system reply", msg_type="systemEventMessage"),
            _graph_msg("r-1", "2026-06-11T10:00:00Z", content="real reply"),
        ]
        sys_root = _graph_msg("sys-root", "2026-06-11T08:00:00Z", content="member added", msg_type="unknownFutureValue")
        sys_root["replies"] = []
        _mock_team_and_channel(httpx_mock)
        httpx_mock.add_response(url=MESSAGES_URL, json={"value": [root, sys_root]})

        teams_channels.run(client, storage, {}, _config(), ctx)

        content = storage.read_file(md_path)
        assert "real root" in content
        assert "real reply" in content
        assert "system reply" not in content
        assert "member added" not in content

    def test_legacy_delta_state_keys_pruned(self, httpx_mock: HTTPXMock, storage, client, ctx):
        _mock_team_and_channel(httpx_mock)
        httpx_mock.add_response(url=MESSAGES_URL, json={"value": []})
        state = {f"delta_{TEAM_ID}_{CHANNEL_ID}": "https://graph.microsoft.com/delta?token=stale"}

        state, _ = teams_channels.run(client, storage, state, _config(), ctx)

        assert not any(key.startswith("delta_") for key in state)

    def test_empty_channel_writes_nothing(self, httpx_mock: HTTPXMock, storage, client, ctx):
        _mock_team_and_channel(httpx_mock)
        httpx_mock.add_response(url=MESSAGES_URL, json={"value": []})

        state, count = teams_channels.run(client, storage, {}, _config(), ctx)

        assert count == 0
        assert storage.list_files(ctx.paths.inbox_root("teams_channels")) == []


class TestEarlyStop:
    def test_early_stop_makes_no_further_page_request(self, httpx_mock: HTTPXMock, storage, client, ctx, store_path):
        """Once a chain at or below the watermark appears, later pages must NOT be requested."""
        watermark = "2026-06-11T12:00:00Z"
        old_root = _graph_msg("root-old", "2026-06-10T09:00:00Z", last_modified="2026-06-10T09:00:00Z")
        old_root["replies"] = []
        page_two = f"{GRAPH_BASE_URL}/teams/{TEAM_ID}/channels/{CHANNEL_ID}/messages?$skip=50"
        _mock_team_and_channel(httpx_mock)
        # Page 1 contains only a stale chain but advertises a next page.
        httpx_mock.add_response(url=MESSAGES_URL, json={"value": [old_root], "@odata.nextLink": page_two})
        # Page 2 is intentionally NOT mocked: requesting it would fail the test.

        state = {"watermarks": {WATERMARK_KEY: watermark}}
        # Seed the store so the watermark is honoured (store missing → backfill).
        storage.write_file(store_path, "")

        _, count = teams_channels.run(client, storage, state, _config(), ctx)

        assert count == 0
        message_requests = [r for r in httpx_mock.get_requests() if f"/channels/{CHANNEL_ID}/messages" in str(r.url)]
        assert len(message_requests) == 1
        assert state["watermarks"][WATERMARK_KEY] == watermark

    def test_merge_preserves_old_messages_on_incremental(self, httpx_mock: HTTPXMock, storage, client, ctx, md_path):
        """Channel regression: an incremental fetch returning only new chains keeps old history."""
        old_root = _graph_msg("root-old", "2026-06-10T09:00:00Z", content="old channel history")
        old_root["replies"] = []
        _mock_team_and_channel(httpx_mock)
        httpx_mock.add_response(url=MESSAGES_URL, json={"value": [old_root]})
        state, _ = teams_channels.run(client, storage, {}, _config(), ctx)

        new_root = _graph_msg("root-new", "2026-06-12T09:00:00Z", content="fresh thread")
        new_root["replies"] = []
        _mock_team_and_channel(httpx_mock)
        httpx_mock.add_response(
            url=MESSAGES_URL,
            json={"value": [new_root, old_root], "@odata.nextLink": f"{GRAPH_BASE_URL}/unused-page-2"},
        )

        teams_channels.run(client, storage, state, _config(), ctx)

        content = storage.read_file(md_path)
        assert "old channel history" in content
        assert "fresh thread" in content


class TestBackfillCap:
    def test_cap_hit_sets_history_complete_false_and_stops(self, httpx_mock: HTTPXMock, storage, client, ctx, md_path):
        root = _graph_msg("root-1", "2026-06-11T09:00:00Z")
        root["replies"] = [
            _graph_msg("r-1", "2026-06-11T10:00:00Z"),
            _graph_msg("r-2", "2026-06-11T11:00:00Z"),
        ]
        page_two = f"{GRAPH_BASE_URL}/teams/{TEAM_ID}/channels/{CHANNEL_ID}/messages?$skip=50"
        _mock_team_and_channel(httpx_mock)
        # Advertises another page, but the cap (2) is exhausted by the first chain (3 messages).
        httpx_mock.add_response(url=MESSAGES_URL, json={"value": [root], "@odata.nextLink": page_two})

        teams_channels.run(client, storage, {}, _config(max_messages=2), ctx)

        content = storage.read_file(md_path)
        assert "history_complete: false" in content

    def test_full_backfill_sets_history_complete_true(self, httpx_mock: HTTPXMock, storage, client, ctx, md_path):
        root = _graph_msg("root-1", "2026-06-11T09:00:00Z")
        root["replies"] = []
        _mock_team_and_channel(httpx_mock)
        httpx_mock.add_response(url=MESSAGES_URL, json={"value": [root]})

        teams_channels.run(client, storage, {}, _config(), ctx)

        content = storage.read_file(md_path)
        assert "history_complete: true" in content
        assert "message_count: 1" in content


class TestEmptyStoreInvariant:
    """A watermark without a store file causes endless re-backfills (B5)."""

    def _backfill_system_only(self, httpx_mock: HTTPXMock, storage, client, ctx) -> dict:
        sys_root = _graph_msg("sys-1", "2026-06-11T09:00:00Z", content="call ended", msg_type="systemEventMessage")
        sys_root["replies"] = []
        _mock_team_and_channel(httpx_mock)
        httpx_mock.add_response(url=MESSAGES_URL, json={"value": [sys_root]})
        state, count = teams_channels.run(client, storage, {}, _config(), ctx)
        assert count == 0
        return state

    def test_system_only_backfill_creates_empty_store_and_no_markdown(
        self, httpx_mock: HTTPXMock, storage, client, ctx, md_path, store_path
    ):
        state = self._backfill_system_only(httpx_mock, storage, client, ctx)

        assert storage.file_exists(store_path)
        assert not storage.file_exists(md_path)
        assert state["watermarks"][WATERMARK_KEY] == "2026-06-11T09:00:00Z"

    def test_second_run_is_incremental_without_re_backfill(self, httpx_mock: HTTPXMock, storage, client, ctx):
        state = self._backfill_system_only(httpx_mock, storage, client, ctx)

        sys_root = _graph_msg("sys-1", "2026-06-11T09:00:00Z", content="call ended", msg_type="systemEventMessage")
        sys_root["replies"] = []
        _mock_team_and_channel(httpx_mock)
        httpx_mock.add_response(url=MESSAGES_URL, json={"value": [sys_root]})
        warnings: list[str] = []
        with patch.object(teams_channels.log, "warning", side_effect=lambda e, **kw: warnings.append(e)):
            teams_channels.run(client, storage, state, _config(), ctx)

        assert "teams_channels.store_missing_backfill" not in warnings


class TestStoreMissingBackfill:
    def test_watermark_reset_when_store_file_missing(self, httpx_mock: HTTPXMock, storage, client, ctx, md_path):
        """When a watermark exists but the .jsonl store file is gone, the watermark
        must be reset to None so a full re-fetch is triggered instead of an
        incremental one."""
        root = _graph_msg("root-1", "2026-06-11T09:00:00Z", content="backfilled")
        root["replies"] = []
        _mock_team_and_channel(httpx_mock)
        httpx_mock.add_response(url=MESSAGES_URL, json={"value": [root]})

        state = {"watermarks": {WATERMARK_KEY: "2026-06-10T12:00:00Z"}}

        warnings: list[str] = []
        with patch.object(teams_channels.log, "warning", side_effect=lambda e, **kw: warnings.append(e)):
            state, count = teams_channels.run(client, storage, state, _config(), ctx)

        assert "teams_channels.store_missing_backfill" in warnings
        assert count == 1
        content = storage.read_file(md_path)
        assert "backfilled" in content


class TestGraphApiErrorOnFetch:
    def test_graph_api_error_skips_channel(self, httpx_mock: HTTPXMock, storage, client, ctx):
        """A GraphApiError from _fetch_chains (e.g. 403 on a private channel) must
        log a warning and skip the channel without crashing the sync cycle."""
        _mock_team_and_channel(httpx_mock)
        httpx_mock.add_response(url=MESSAGES_URL, status_code=403, json={"error": {"message": "Forbidden"}})

        warnings: list[str] = []
        with patch.object(teams_channels.log, "warning", side_effect=lambda e, **kw: warnings.append(e)):
            state, count = teams_channels.run(client, storage, {}, _config(), ctx)

        assert count == 0
        assert "teams_channels.fetch_failed" in warnings
        assert WATERMARK_KEY not in state["watermarks"]


class TestPerChannelIsolation:
    def test_transport_error_in_replies_pagination_skips_channel(self, httpx_mock: HTTPXMock, storage, client, ctx):
        """A TransportError escaping replies pagination must not kill the sync cycle."""
        replies_next = f"{GRAPH_BASE_URL}/teams/{TEAM_ID}/channels/{CHANNEL_ID}/messages/root-1/replies?$skip=1"
        root = _graph_msg("root-1", "2026-06-11T09:00:00Z")
        root["replies"] = []
        root["replies@odata.nextLink"] = replies_next
        _mock_team_and_channel(httpx_mock)
        httpx_mock.add_response(url=MESSAGES_URL, json={"value": [root]})
        httpx_mock.add_exception(httpx.ConnectError("network down"), url=replies_next, is_reusable=True)

        errors: list[str] = []
        with patch.object(teams_channels.log, "error", side_effect=lambda e, **kw: errors.append(e)):
            state, count = teams_channels.run(client, storage, {}, _config(), ctx)

        assert count == 0
        assert "teams_channels.fetch_transport_error" in errors
        assert WATERMARK_KEY not in state["watermarks"]

    def test_transport_error_in_hosted_content_listing_skips_channel(self, httpx_mock: HTTPXMock, storage, client, ctx):
        config = TeamsChannelsExtractorConfig(
            enabled=True,
            poll_interval_minutes=5,
            max_messages_per_channel=200,
            download_attachments=False,
            download_inline_images=True,
            max_attachment_size_mb=25,
            attachment_convert_extensions=[],
            channels=None,
        )
        root = _graph_msg("root-1", "2026-06-11T09:00:00Z")
        root["replies"] = []
        _mock_team_and_channel(httpx_mock)
        httpx_mock.add_response(url=MESSAGES_URL, json={"value": [root]})
        httpx_mock.add_exception(
            httpx.ConnectError("network down"), url=re.compile(r".*/hostedContents.*"), is_reusable=True
        )

        errors: list[str] = []
        with patch.object(teams_channels.log, "error", side_effect=lambda e, **kw: errors.append(e)):
            state, count = teams_channels.run(client, storage, {}, config, ctx)

        assert count == 0
        assert "teams_channels.media_transport_error" in errors
        assert WATERMARK_KEY not in state["watermarks"]

    def test_corrupt_store_skips_channel_without_advancing_watermark(
        self, httpx_mock: HTTPXMock, storage, client, ctx, store_path
    ):
        storage.write_file(store_path, "{not valid json\n")
        state = {"watermarks": {WATERMARK_KEY: "2026-06-10T09:00:00Z"}}
        root = _graph_msg("root-new", "2026-06-12T09:00:00Z")
        root["replies"] = []
        _mock_team_and_channel(httpx_mock)
        httpx_mock.add_response(url=MESSAGES_URL, json={"value": [root]})

        errors: list[str] = []
        with patch.object(teams_channels.log, "error", side_effect=lambda e, **kw: errors.append(e)):
            state, count = teams_channels.run(client, storage, state, _config(), ctx)

        assert count == 0
        assert "teams_channels.store_corrupt" in errors
        assert state["watermarks"][WATERMARK_KEY] == "2026-06-10T09:00:00Z"


class TestHistoryCompleteDefault:
    def test_unknown_history_completeness_renders_false(self, httpx_mock: HTTPXMock, storage, client, ctx, md_path):
        """Pessimistic default: a missing history_complete state key must render as false."""
        old_root = _graph_msg("root-old", "2026-06-10T09:00:00Z")
        old_root["replies"] = []
        _mock_team_and_channel(httpx_mock)
        httpx_mock.add_response(url=MESSAGES_URL, json={"value": [old_root]})
        state, _ = teams_channels.run(client, storage, {}, _config(), ctx)
        state["history_complete"].pop(WATERMARK_KEY)

        new_root = _graph_msg("root-new", "2026-06-12T09:00:00Z")
        new_root["replies"] = []
        _mock_team_and_channel(httpx_mock)
        httpx_mock.add_response(url=MESSAGES_URL, json={"value": [new_root, old_root]})
        teams_channels.run(client, storage, state, _config(), ctx)

        content = storage.read_file(md_path)
        assert "history_complete: false" in content


class TestChannelMedia:
    def test_root_attachment_downloaded_and_linked(
        self, httpx_mock: HTTPXMock, storage, client, ctx, conv_dir, md_path
    ):
        config = TeamsChannelsExtractorConfig(
            enabled=True,
            poll_interval_minutes=5,
            max_messages_per_channel=200,
            download_attachments=True,
            download_inline_images=False,
            max_attachment_size_mb=100,
            attachment_convert_extensions=[],
            channels=None,
        )
        content_url = "https://sanoptis.sharepoint.com/sites/x/spec.pdf"
        root = _graph_msg("root-att", "2026-06-11T09:00:00Z", content="see attached")
        root["replies"] = []
        root["attachments"] = [{"contentType": "reference", "name": "spec.pdf", "contentUrl": content_url}]
        _mock_team_and_channel(httpx_mock)
        httpx_mock.add_response(url=MESSAGES_URL, json={"value": [root]})
        httpx_mock.add_response(
            url=re.compile(r".*/shares/.*/driveItem.*"),
            json={"id": "di", "size": 64, "@microsoft.graph.downloadUrl": "https://sanoptis.sharepoint.com/dl?t=x"},
        )
        httpx_mock.add_response(url=re.compile(r"https://sanoptis\.sharepoint\.com/dl.*"), content=b"%PDF fake")

        teams_channels.run(client, storage, {}, config, ctx)

        content = storage.read_file(md_path)
        assert "[spec.pdf](attachments/root-att/spec.pdf)" in content
        assert ctx.paths.attachment(conv_dir, "root-att", "spec.pdf") in storage.list_files(
            ctx.paths.inbox_root("teams_channels")
        )

    def test_reply_inline_image_uses_replies_hosted_content_route(
        self, httpx_mock: HTTPXMock, storage, client, ctx, conv_dir
    ):
        config = TeamsChannelsExtractorConfig(
            enabled=True,
            poll_interval_minutes=5,
            max_messages_per_channel=200,
            download_attachments=False,
            download_inline_images=True,
            max_attachment_size_mb=100,
            attachment_convert_extensions=[],
            channels=None,
        )
        root = _graph_msg("root-1", "2026-06-11T09:00:00Z")
        root["replies"] = [_graph_msg("r-1", "2026-06-11T10:00:00Z", content="reply with image")]
        _mock_team_and_channel(httpx_mock)
        httpx_mock.add_response(url=MESSAGES_URL, json={"value": [root]})
        root_hc = f"/teams/{TEAM_ID}/channels/{CHANNEL_ID}/messages/root-1/hostedContents"
        reply_hc = f"/teams/{TEAM_ID}/channels/{CHANNEL_ID}/messages/root-1/replies/r-1/hostedContents"
        httpx_mock.add_response(url=re.compile(rf".*{re.escape(root_hc)}\?.*"), json={"value": []})
        httpx_mock.add_response(url=re.compile(rf".*{re.escape(reply_hc)}\?.*"), json={"value": [{"id": "HID-1"}]})
        httpx_mock.add_response(
            url=re.compile(rf".*{re.escape(reply_hc)}/HID-1/\$value.*"),
            content=b"\x89PNG\r\n\x1a\n",
            headers={"Content-Type": "image/png"},
        )

        teams_channels.run(client, storage, {}, config, ctx)

        assert ctx.paths.attachment(conv_dir, "r-1", "inline_0.png") in storage.list_files(
            ctx.paths.inbox_root("teams_channels")
        )


class TestExplicitMode:
    """channels configured explicitly — no discovery Graph calls (ChannelMessage.Read.All only)."""

    def _explicit_config(self) -> TeamsChannelsExtractorConfig:
        return _config(
            channels=[
                ExplicitChannel(
                    team_id=TEAM_ID,
                    channel_id=CHANNEL_ID,
                    team_name="Engineering",
                    channel_name="General",
                )
            ]
        )

    def test_explicit_mode_performs_no_discovery_requests(self, httpx_mock: HTTPXMock, storage, client, ctx):
        """Neither /me/joinedTeams nor /teams/{id}/channels may be requested.

        Those endpoints are intentionally NOT mocked — pytest-httpx fails the
        test on any unmatched request, so discovery calls cannot slip through.
        """
        root = _graph_msg("root-1", "2026-06-11T09:00:00Z", content="explicit root")
        root["replies"] = []
        httpx_mock.add_response(url=MESSAGES_URL, json={"value": [root]})

        teams_channels.run(client, storage, {}, self._explicit_config(), ctx)

        urls = [str(r.url) for r in httpx_mock.get_requests()]
        assert not any("/me/joinedTeams" in url for url in urls)
        assert not any(f"/teams/{TEAM_ID}/channels?" in url for url in urls)

    def test_explicit_mode_syncs_end_to_end(self, httpx_mock: HTTPXMock, storage, client, ctx, md_path, store_path):
        root = _graph_msg("root-1", "2026-06-11T09:00:00Z", content="explicit root", subject="Planning")
        root["replies"] = [_graph_msg("r-1", "2026-06-11T10:00:00Z", content="explicit reply", sender="Bob")]
        httpx_mock.add_response(url=MESSAGES_URL, json={"value": [root]})

        state, count = teams_channels.run(client, storage, {}, self._explicit_config(), ctx)

        assert count == 1
        content = storage.read_file(md_path)
        assert "# Engineering / General" in content
        assert "explicit root" in content
        assert "explicit reply" in content
        assert len(load_store(storage, store_path)) == 2
        assert state["watermarks"][WATERMARK_KEY] == "2026-06-11T10:00:00Z"

    def test_explicit_mode_folder_slugs_from_configured_names(self, httpx_mock: HTTPXMock, storage, client, ctx):
        config = _config(
            channels=[
                ExplicitChannel(
                    team_id=TEAM_ID,
                    channel_id=CHANNEL_ID,
                    team_name="Medical Data & AI",
                    channel_name="Allgemein",
                )
            ]
        )
        root = _graph_msg("root-1", "2026-06-11T09:00:00Z")
        root["replies"] = []
        httpx_mock.add_response(url=MESSAGES_URL, json={"value": [root]})

        teams_channels.run(client, storage, {}, config, ctx)

        expected_dir = ctx.paths.inbox_item(
            "teams_channels",
            slugify("Medical Data & AI", 80),
            f"{slugify('Allgemein', 80)}-{short_hash(CHANNEL_ID, 6)}",
        )
        assert storage.file_exists(ctx.paths.conversation_file(expected_dir))

    def test_explicit_mode_second_run_is_incremental(self, httpx_mock: HTTPXMock, storage, client, ctx):
        """The watermark set by an explicit-mode backfill drives early-stop on the next run."""
        root = _graph_msg("root-1", "2026-06-11T09:00:00Z", content="explicit root")
        root["replies"] = []
        httpx_mock.add_response(url=MESSAGES_URL, json={"value": [root]})
        state, _ = teams_channels.run(client, storage, {}, self._explicit_config(), ctx)

        page_two = f"{GRAPH_BASE_URL}/teams/{TEAM_ID}/channels/{CHANNEL_ID}/messages?$skip=50"
        httpx_mock.add_response(url=MESSAGES_URL, json={"value": [root], "@odata.nextLink": page_two})
        # Page 2 is intentionally NOT mocked: requesting it would fail the test.

        _, count = teams_channels.run(client, storage, state, self._explicit_config(), ctx)

        assert count == 0
        assert state["watermarks"][WATERMARK_KEY] == "2026-06-11T09:00:00Z"


class TestOrphanedReplies:
    def test_reply_to_skipped_root_renders_as_orphan(self, httpx_mock: HTTPXMock, storage, client, ctx, md_path):
        sys_root = _graph_msg("sys-root", "2026-06-11T08:00:00Z", msg_type="systemEventMessage")
        sys_root["replies"] = [_graph_msg("r-1", "2026-06-11T10:00:00Z", content="reply to system root")]
        _mock_team_and_channel(httpx_mock)
        httpx_mock.add_response(url=MESSAGES_URL, json={"value": [sys_root]})

        teams_channels.run(client, storage, {}, _config(), ctx)

        content = storage.read_file(md_path)
        assert "*(orphaned reply)*" in content
        assert "reply to system root" in content


class TestAdvanceChannelWatermark:
    def test_advance_sets_max_chain_modified(self):
        state: dict = {"watermarks": {}}
        chains: list[tuple[dict, list[dict]]] = [
            (
                {"lastModifiedDateTime": "2026-06-11T09:00:00Z", "createdDateTime": "2026-06-11T09:00:00Z"},
                [{"lastModifiedDateTime": "2026-06-11T12:00:00Z", "createdDateTime": "2026-06-11T10:00:00Z"}],
            ),
        ]
        teams_channels._advance_channel_watermark(state, "team:ch", chains, None)
        assert state["watermarks"]["team:ch"] == "2026-06-11T12:00:00Z"

    def test_advance_keeps_max_of_old_and_new(self):
        state: dict = {"watermarks": {"team:ch": "2026-06-12T00:00:00Z"}}
        chains: list[tuple[dict, list[dict]]] = [
            (
                {"lastModifiedDateTime": "2026-06-11T09:00:00Z", "createdDateTime": "2026-06-11T09:00:00Z"},
                [],
            ),
        ]
        teams_channels._advance_channel_watermark(state, "team:ch", chains, "2026-06-12T00:00:00Z")
        assert state["watermarks"]["team:ch"] == "2026-06-12T00:00:00Z"

    def test_multiple_chains_picks_max(self):
        state: dict = {"watermarks": {}}
        chains: list[tuple[dict, list[dict]]] = [
            (
                {"lastModifiedDateTime": "2026-06-11T09:00:00Z", "createdDateTime": "2026-06-11T09:00:00Z"},
                [],
            ),
            (
                {"lastModifiedDateTime": "2026-06-11T14:00:00Z", "createdDateTime": "2026-06-11T14:00:00Z"},
                [{"lastModifiedDateTime": "2026-06-11T15:00:00Z", "createdDateTime": "2026-06-11T14:30:00Z"}],
            ),
        ]
        teams_channels._advance_channel_watermark(state, "team:ch", chains, None)
        assert state["watermarks"]["team:ch"] == "2026-06-11T15:00:00Z"


class TestStorePath:
    """The store the extractor writes must be the one the resolver names."""

    def test_matches_path_run_actually_writes(self, httpx_mock: HTTPXMock, storage, client, ctx, store_path) -> None:
        """Guards the derivation against drifting from what the extractor writes."""
        _mock_team_and_channel(httpx_mock)
        httpx_mock.add_response(url=MESSAGES_URL, json={"value": [_graph_msg("m1", "2026-06-11T09:00:00Z")]})

        teams_channels.run(client, storage, {}, _config(), ctx)

        assert store_path in storage.list_files(ctx.paths.inbox_root("teams_channels"))


class TestNonDefaultLayout:
    """Golden path under a renamed layout — the names come from config, or nowhere."""

    def test_conversation_and_store_land_under_the_configured_names(
        self, httpx_mock: HTTPXMock, storage, client, odd_ctx
    ):
        root = _graph_msg("root-1", "2026-06-11T09:00:00Z", content="root message", subject="Planning")
        root["replies"] = [_graph_msg("r-1", "2026-06-11T10:00:00Z", content="first reply", sender="Bob")]
        _mock_team_and_channel(httpx_mock)
        httpx_mock.add_response(url=MESSAGES_URL, json={"value": [root]})

        _, count = teams_channels.run(client, storage, {}, _config(), odd_ctx)

        conv = _conv_dir(odd_ctx.paths)
        assert count == 1
        assert storage.list_files("") == [
            odd_ctx.paths.conversation_file(conv),
            odd_ctx.paths.conversation_store(conv),
        ]
        # The conventional names appear nowhere: a hardcoded literal would land here.
        assert storage.list_files("inbox/teams-channels") == []


class TestChannelInfo:
    def test_is_frozen(self):
        """Callees receive ChannelInfo by reference; mutation must be impossible."""
        info = teams_channels.ChannelInfo(team_name="Eng", channel_name="General", channel_id=CHANNEL_ID)
        with pytest.raises(FrozenInstanceError):
            info.channel_name = "Other"  # type: ignore[misc]
