"""Tests for the merge-based Teams chat extractor (Teams Sync v2)."""

from __future__ import annotations

import re
from unittest.mock import patch
from urllib.parse import unquote_plus

import httpx
import pytest
from pytest_httpx import HTTPXMock

from m365_extract.config import GraphConfig, TeamsChatsExtractorConfig
from m365_extract.extractors import teams_chats
from m365_extract.extractors._message_store import load_store
from m365_extract.graph_client import GRAPH_BASE_URL, GraphClient
from m365_extract.markdown_writer import short_hash, slugify
from m365_extract.storage.local import LocalBackend

CHAT_ID = "19:chat-1"
CHAT_DIR = f"teams-chats/alice-bob_{short_hash(CHAT_ID, 6)}"


def _chats_response(chat_id: str = CHAT_ID) -> dict:
    return {
        "value": [
            {
                "id": chat_id,
                "chatType": "oneOnOne",
                "topic": None,
                "members": [{"displayName": "Alice"}, {"displayName": "Bob"}],
            }
        ]
    }


def _graph_msg(
    msg_id: str,
    created: str,
    *,
    content: str = "hello",
    etag: str = "1",
    last_modified: str | None = None,
    msg_type: str = "message",
    sender: str = "Alice",
    edited: bool = False,
    deleted: bool = False,
) -> dict:
    return {
        "id": msg_id,
        "messageType": msg_type,
        "createdDateTime": created,
        "lastModifiedDateTime": last_modified if last_modified else created,
        "etag": etag,
        "lastEditedDateTime": created if edited else None,
        "deletedDateTime": created if deleted else None,
        "from": {"user": {"displayName": sender, "id": "u1"}},
        "body": {"contentType": "text", "content": content},
    }


def _config(max_messages: int = 200) -> TeamsChatsExtractorConfig:
    return TeamsChatsExtractorConfig(
        enabled=True,
        poll_interval_minutes=5,
        max_messages_per_chat=max_messages,
        download_attachments=False,
        download_inline_images=False,
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
def storage(tmp_path):
    return LocalBackend(str(tmp_path / "vault"))


@pytest.fixture()
def client(graph_config):
    c = GraphClient(graph_config, lambda: "test-token")
    yield c
    c.close()


def _mock_chats(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(url=re.compile(r".*/me/chats\?.*"), json=_chats_response())


class TestBackfill:
    def test_backfill_paginates_to_exhaustion(self, httpx_mock: HTTPXMock, storage, client):
        _mock_chats(httpx_mock)
        next_link = f"{GRAPH_BASE_URL}/me/chats/{CHAT_ID}/messages?$skip=50"
        httpx_mock.add_response(
            url=re.compile(r".*/me/chats/.*/messages\?.*"),
            json={
                "value": [_graph_msg("m2", "2026-06-11T10:00:00Z", content="newer")],
                "@odata.nextLink": next_link,
            },
        )
        httpx_mock.add_response(
            url=next_link,
            json={"value": [_graph_msg("m1", "2026-06-10T09:00:00Z", content="older")]},
        )

        state, count = teams_chats.run(client, storage, {}, _config(), {})

        assert count == 1
        content = storage.read_file(f"{CHAT_DIR}/messages.md")
        assert "newer" in content and "older" in content
        assert len(load_store(storage, f"{CHAT_DIR}/messages.jsonl")) == 2

    def test_backfill_request_has_no_filter(self, httpx_mock: HTTPXMock, storage, client):
        _mock_chats(httpx_mock)
        httpx_mock.add_response(
            url=re.compile(r".*/me/chats/.*/messages.*"),
            json={"value": [_graph_msg("m1", "2026-06-11T09:00:00Z")]},
        )

        teams_chats.run(client, storage, {}, _config(), {})

        msg_request = [r for r in httpx_mock.get_requests() if "/messages" in str(r.url)][0]
        assert "$filter" not in unquote_plus(str(msg_request.url))

    def test_watermark_set_to_max_last_modified(self, httpx_mock: HTTPXMock, storage, client):
        _mock_chats(httpx_mock)
        httpx_mock.add_response(
            url=re.compile(r".*/me/chats/.*/messages.*"),
            json={
                "value": [
                    _graph_msg("m2", "2026-06-11T10:00:00Z", last_modified="2026-06-11T12:00:00Z"),
                    _graph_msg("m1", "2026-06-11T09:00:00Z"),
                ]
            },
        )

        state, _ = teams_chats.run(client, storage, {}, _config(), {})

        assert state["watermarks"][CHAT_ID] == "2026-06-11T12:00:00Z"

    def test_cap_hit_sets_history_complete_false(self, httpx_mock: HTTPXMock, storage, client):
        """history_complete pins the truncation signal: a nextLink remained at the backfill page cap."""
        _mock_chats(httpx_mock)
        # cap=2 → one-page budget; the page advertises another page that is never fetched.
        httpx_mock.add_response(
            url=re.compile(r".*/me/chats/.*/messages.*"),
            json={
                "value": [
                    _graph_msg("m2", "2026-06-11T10:00:00Z"),
                    _graph_msg("m1", "2026-06-11T09:00:00Z"),
                ],
                "@odata.nextLink": f"{GRAPH_BASE_URL}/me/chats/{CHAT_ID}/messages?$skip=50",
            },
        )

        teams_chats.run(client, storage, {}, _config(max_messages=2), {})

        content = storage.read_file(f"{CHAT_DIR}/messages.md")
        assert "history_complete: false" in content
        assert "message_limit_reached" not in content

    def test_count_at_cap_without_next_link_is_complete(self, httpx_mock: HTTPXMock, storage, client):
        """Fetching exactly cap messages with no nextLink means the history IS complete."""
        _mock_chats(httpx_mock)
        httpx_mock.add_response(
            url=re.compile(r".*/me/chats/.*/messages.*"),
            json={
                "value": [
                    _graph_msg("m2", "2026-06-11T10:00:00Z"),
                    _graph_msg("m1", "2026-06-11T09:00:00Z"),
                ]
            },
        )

        teams_chats.run(client, storage, {}, _config(max_messages=2), {})

        content = storage.read_file(f"{CHAT_DIR}/messages.md")
        assert "history_complete: true" in content

    def test_full_backfill_sets_history_complete_true(self, httpx_mock: HTTPXMock, storage, client):
        _mock_chats(httpx_mock)
        httpx_mock.add_response(
            url=re.compile(r".*/me/chats/.*/messages.*"),
            json={"value": [_graph_msg("m1", "2026-06-11T09:00:00Z")]},
        )

        teams_chats.run(client, storage, {}, _config(), {})

        content = storage.read_file(f"{CHAT_DIR}/messages.md")
        assert "history_complete: true" in content
        assert "message_count: 1" in content


class TestIncremental:
    def _seed(self, httpx_mock: HTTPXMock, storage, client) -> dict:
        """Backfill one old message, returning the resulting state."""
        _mock_chats(httpx_mock)
        httpx_mock.add_response(
            url=re.compile(r".*/me/chats/.*/messages.*"),
            json={"value": [_graph_msg("old-1", "2026-06-10T09:00:00Z", content="old history")]},
        )
        state, _ = teams_chats.run(client, storage, {}, _config(), {})
        return state

    def test_incremental_pairs_filter_with_orderby_on_last_modified(self, httpx_mock: HTTPXMock, storage, client):
        """The central v1 bug: $filter is silently ignored unless $orderby targets the same property."""
        state = self._seed(httpx_mock, storage, client)

        _mock_chats(httpx_mock)
        httpx_mock.add_response(
            url=re.compile(r".*/me/chats/.*/messages.*"),
            json={"value": [_graph_msg("new-1", "2026-06-11T10:00:00Z", content="brand new")]},
        )
        teams_chats.run(client, storage, state, _config(), {})

        incremental_request = [r for r in httpx_mock.get_requests() if "/messages" in str(r.url)][-1]
        url = unquote_plus(str(incremental_request.url))
        assert "$orderby=lastModifiedDateTime desc" in url
        assert "$filter=lastModifiedDateTime gt 2026-06-10T09:00:00Z" in url

    def test_merge_preserves_messages_absent_from_fetch(self, httpx_mock: HTTPXMock, storage, client):
        """The v1 data-loss regression: old messages must survive an incremental fetch."""
        state = self._seed(httpx_mock, storage, client)

        _mock_chats(httpx_mock)
        httpx_mock.add_response(
            url=re.compile(r".*/me/chats/.*/messages.*"),
            json={"value": [_graph_msg("new-1", "2026-06-11T10:00:00Z", content="brand new")]},
        )
        teams_chats.run(client, storage, state, _config(), {})

        content = storage.read_file(f"{CHAT_DIR}/messages.md")
        assert "old history" in content
        assert "brand new" in content
        assert len(load_store(storage, f"{CHAT_DIR}/messages.jsonl")) == 2

    def test_no_write_when_nothing_changed(self, httpx_mock: HTTPXMock, storage, client):
        state = self._seed(httpx_mock, storage, client)
        before = storage.read_file(f"{CHAT_DIR}/messages.md")

        _mock_chats(httpx_mock)
        httpx_mock.add_response(url=re.compile(r".*/me/chats/.*/messages.*"), json={"value": []})

        writes: list[str] = []
        original_write = storage.write_file
        storage.write_file = lambda path, content: writes.append(path) or original_write(path, content)
        _, count = teams_chats.run(client, storage, state, _config(), {})

        assert count == 0
        assert writes == []
        assert storage.read_file(f"{CHAT_DIR}/messages.md") == before

    def test_refetched_unchanged_etag_does_not_rewrite(self, httpx_mock: HTTPXMock, storage, client):
        state = self._seed(httpx_mock, storage, client)

        _mock_chats(httpx_mock)
        httpx_mock.add_response(
            url=re.compile(r".*/me/chats/.*/messages.*"),
            json={"value": [_graph_msg("old-1", "2026-06-10T09:00:00Z", content="old history")]},
        )
        writes: list[str] = []
        original_write = storage.write_file
        storage.write_file = lambda path, content: writes.append(path) or original_write(path, content)
        _, count = teams_chats.run(client, storage, state, _config(), {})

        assert count == 0
        assert writes == []

    def test_edited_message_replaces_content(self, httpx_mock: HTTPXMock, storage, client):
        state = self._seed(httpx_mock, storage, client)

        _mock_chats(httpx_mock)
        httpx_mock.add_response(
            url=re.compile(r".*/me/chats/.*/messages.*"),
            json={"value": [_graph_msg("old-1", "2026-06-10T09:00:00Z", content="corrected", etag="2", edited=True)]},
        )
        teams_chats.run(client, storage, state, _config(), {})

        content = storage.read_file(f"{CHAT_DIR}/messages.md")
        assert "corrected" in content
        assert "old history" not in content
        assert "*(edited)*" in content

    def test_incremental_uses_global_page_bound_not_backfill_cap(self, httpx_mock: HTTPXMock, storage, client):
        """A 2-page incremental window must be fetched fully even when the backfill cap is one page."""
        config = _config(max_messages=50)  # backfill budget: exactly one page
        _mock_chats(httpx_mock)
        httpx_mock.add_response(
            url=re.compile(r".*/me/chats/.*/messages.*"),
            json={"value": [_graph_msg("old-1", "2026-06-10T09:00:00Z")]},
        )
        state, _ = teams_chats.run(client, storage, {}, config, {})

        _mock_chats(httpx_mock)
        page_two = f"{GRAPH_BASE_URL}/me/chats/{CHAT_ID}/messages?$skip=50"
        httpx_mock.add_response(
            url=re.compile(r".*/me/chats/.*/messages\?.*"),
            json={
                "value": [_graph_msg("new-2", "2026-06-11T11:00:00Z")],
                "@odata.nextLink": page_two,
            },
        )
        httpx_mock.add_response(url=page_two, json={"value": [_graph_msg("new-1", "2026-06-11T10:00:00Z")]})

        teams_chats.run(client, storage, state, config, {})

        store = load_store(storage, f"{CHAT_DIR}/messages.jsonl")
        assert {"old-1", "new-1", "new-2"} <= set(store)
        assert state["watermarks"][CHAT_ID] == "2026-06-11T11:00:00Z"

    def test_incremental_truncation_at_global_bound_does_not_advance_watermark(
        self, httpx_mock: HTTPXMock, storage, graph_config
    ):
        """Loud refusal: when even the global page bound truncates an incremental fetch,
        log an error and keep the old watermark so the next cycle retries."""
        config_one_page = GraphConfig(
            max_retries=1,
            backoff_base_ms=10,
            timeout_seconds=5,
            max_pages=1,
            max_retry_after_seconds=300.0,
            error_message_max_length=200,
        )
        client = GraphClient(config_one_page, lambda: "test-token")
        try:
            state = self._seed(httpx_mock, storage, client)
            assert state["watermarks"][CHAT_ID] == "2026-06-10T09:00:00Z"

            _mock_chats(httpx_mock)
            httpx_mock.add_response(
                url=re.compile(r".*/me/chats/.*/messages.*"),
                json={
                    "value": [_graph_msg("new-1", "2026-06-11T10:00:00Z", content="partial window")],
                    "@odata.nextLink": f"{GRAPH_BASE_URL}/me/chats/{CHAT_ID}/messages?$skip=50",
                },
            )
            errors: list[str] = []
            with patch.object(teams_chats.log, "error", side_effect=lambda e, **kw: errors.append(e)):
                teams_chats.run(client, storage, state, _config(), {})

            assert "teams_chats.incremental_truncated" in errors
            assert state["watermarks"][CHAT_ID] == "2026-06-10T09:00:00Z"
            # Fetched messages are still merged — only the watermark is held back.
            assert "new-1" in load_store(storage, f"{CHAT_DIR}/messages.jsonl")
        finally:
            client.close()

    def test_unknown_history_completeness_renders_false(self, httpx_mock: HTTPXMock, storage, client):
        """Pessimistic default: a missing history_complete state key must render as false."""
        state = self._seed(httpx_mock, storage, client)
        state["history_complete"].pop(CHAT_ID)

        _mock_chats(httpx_mock)
        httpx_mock.add_response(
            url=re.compile(r".*/me/chats/.*/messages.*"),
            json={"value": [_graph_msg("new-1", "2026-06-11T10:00:00Z")]},
        )
        teams_chats.run(client, storage, state, _config(), {})

        content = storage.read_file(f"{CHAT_DIR}/messages.md")
        assert "history_complete: false" in content

    def test_missing_store_with_watermark_triggers_backfill(self, httpx_mock: HTTPXMock, storage, client):
        """Manual store deletion drops the watermark — the store is the source of truth."""
        state = {"watermarks": {CHAT_ID: "2026-06-10T09:00:00Z"}}
        _mock_chats(httpx_mock)
        httpx_mock.add_response(
            url=re.compile(r".*/me/chats/.*/messages.*"),
            json={"value": [_graph_msg("m1", "2026-06-11T09:00:00Z")]},
        )

        teams_chats.run(client, storage, state, _config(), {})

        msg_request = [r for r in httpx_mock.get_requests() if "/messages" in str(r.url)][0]
        assert "$filter" not in unquote_plus(str(msg_request.url))


class TestFiltering:
    def test_only_message_type_kept(self, httpx_mock: HTTPXMock, storage, client):
        _mock_chats(httpx_mock)
        httpx_mock.add_response(
            url=re.compile(r".*/me/chats/.*/messages.*"),
            json={
                "value": [
                    _graph_msg("sys-1", "2026-06-11T09:00:00Z", content="system event", msg_type="systemEventMessage"),
                    _graph_msg("unk-1", "2026-06-11T09:30:00Z", content="unknown stuff", msg_type="unknownFutureValue"),
                    _graph_msg("m1", "2026-06-11T10:00:00Z", content="real message"),
                ]
            },
        )

        teams_chats.run(client, storage, {}, _config(), {})

        content = storage.read_file(f"{CHAT_DIR}/messages.md")
        assert "real message" in content
        assert "system event" not in content
        assert "unknown stuff" not in content

    def test_chat_with_no_messages_not_written(self, httpx_mock: HTTPXMock, storage, client):
        _mock_chats(httpx_mock)
        httpx_mock.add_response(url=re.compile(r".*/me/chats/.*/messages.*"), json={"value": []})

        state, count = teams_chats.run(client, storage, {}, _config(), {})

        assert count == 0
        assert storage.list_files("teams-chats") == []

    def test_fetch_failure_continues_to_next_chat(self, httpx_mock: HTTPXMock, storage, client):
        httpx_mock.add_response(
            url=re.compile(r".*/me/chats\?.*"),
            json=_chats_response(),
        )
        httpx_mock.add_response(url=re.compile(r".*/me/chats/.*/messages.*"), status_code=403)

        state, count = teams_chats.run(client, storage, {}, _config(), {})

        assert count == 0


class TestRendering:
    def test_new_standard_format(self, httpx_mock: HTTPXMock, storage, client):
        _mock_chats(httpx_mock)
        httpx_mock.add_response(
            url=re.compile(r".*/me/chats/.*/messages.*"),
            json={
                "value": [
                    _graph_msg("m2", "2026-06-11T10:20:00Z", sender="Samuel Scholl", content="reply", edited=True),
                    _graph_msg("m1", "2026-06-11T09:42:00Z", sender="Matthias Christenson", content="hi"),
                ]
            },
        )

        teams_chats.run(client, storage, {}, _config(), {})

        content = storage.read_file(f"{CHAT_DIR}/messages.md")
        assert "## 2026-06-11" in content
        assert "### 09:42 — Matthias Christenson" in content
        assert "### 10:20 — Samuel Scholl *(edited)*" in content
        assert "## Messages" in content
        assert "# Alice, Bob" in content

    def test_skeleton_sections_present(self, httpx_mock: HTTPXMock, storage, client):
        _mock_chats(httpx_mock)
        httpx_mock.add_response(
            url=re.compile(r".*/me/chats/.*/messages.*"),
            json={"value": [_graph_msg("m1", "2026-06-11T09:42:00Z")]},
        )

        teams_chats.run(client, storage, {}, _config(), {})

        content = storage.read_file(f"{CHAT_DIR}/messages.md")
        assert "## Observations" in content
        assert "- [message_count] 1" in content
        assert content.index("## Observations") < content.index("## Messages")

    def test_deleted_message_renders_tombstone(self, httpx_mock: HTTPXMock, storage, client):
        _mock_chats(httpx_mock)
        httpx_mock.add_response(
            url=re.compile(r".*/me/chats/.*/messages.*"),
            json={"value": [_graph_msg("m1", "2026-06-11T09:42:00Z", content="", deleted=True)]},
        )

        teams_chats.run(client, storage, {}, _config(), {})

        content = storage.read_file(f"{CHAT_DIR}/messages.md")
        assert "*(deleted)*" in content
        assert "*Message deleted.*" in content


class TestAttachments:
    def test_attachment_links_survive_re_render(self, httpx_mock: HTTPXMock, storage, client):
        """AttachmentRefs are stored in the JSONL store so old links persist across renders."""
        config = TeamsChatsExtractorConfig(
            enabled=True,
            poll_interval_minutes=5,
            max_messages_per_chat=200,
            download_attachments=True,
            download_inline_images=False,
            max_attachment_size_mb=100,
            attachment_convert_extensions=[],
        )
        content_url = "https://sanoptis.sharepoint.com/sites/x/spec.pdf"

        _mock_chats(httpx_mock)
        msg = _graph_msg("msg-att", "2026-06-10T09:00:00Z", content="see attached")
        msg["attachments"] = [{"contentType": "reference", "name": "spec.pdf", "contentUrl": content_url}]
        httpx_mock.add_response(url=re.compile(r".*/me/chats/.*/messages.*"), json={"value": [msg]})
        httpx_mock.add_response(
            url=re.compile(r".*/shares/.*/driveItem.*"),
            json={"id": "di", "size": 64, "@microsoft.graph.downloadUrl": "https://sanoptis.sharepoint.com/dl?t=x"},
        )
        httpx_mock.add_response(url=re.compile(r"https://sanoptis\.sharepoint\.com/dl.*"), content=b"%PDF fake")

        state, _ = teams_chats.run(client, storage, {}, config, {})
        content = storage.read_file(f"{CHAT_DIR}/messages.md")
        assert "[spec.pdf](attachments/msg-att/spec.pdf)" in content

        # Incremental run fetching only a new message must keep the old link.
        _mock_chats(httpx_mock)
        httpx_mock.add_response(
            url=re.compile(r".*/me/chats/.*/messages.*"),
            json={"value": [_graph_msg("msg-new", "2026-06-11T10:00:00Z", content="follow-up")]},
        )
        teams_chats.run(client, storage, state, config, {})

        content = storage.read_file(f"{CHAT_DIR}/messages.md")
        assert "[spec.pdf](attachments/msg-att/spec.pdf)" in content
        assert "follow-up" in content

    @staticmethod
    def _attachment_config() -> TeamsChatsExtractorConfig:
        return TeamsChatsExtractorConfig(
            enabled=True,
            poll_interval_minutes=5,
            max_messages_per_chat=200,
            download_attachments=True,
            download_inline_images=False,
            max_attachment_size_mb=100,
            attachment_convert_extensions=[],
        )

    def _seed_with_attachment(self, httpx_mock: HTTPXMock, storage, client) -> dict:
        """Backfill one message carrying spec.pdf; returns the resulting state."""
        content_url = "https://sanoptis.sharepoint.com/sites/x/spec.pdf"
        _mock_chats(httpx_mock)
        msg = _graph_msg("msg-att", "2026-06-10T09:00:00Z", content="see attached")
        msg["attachments"] = [{"contentType": "reference", "name": "spec.pdf", "contentUrl": content_url}]
        httpx_mock.add_response(url=re.compile(r".*/me/chats/.*/messages.*"), json={"value": [msg]})
        httpx_mock.add_response(
            url=re.compile(r".*/shares/.*/driveItem.*"),
            json={"id": "di", "size": 64, "@microsoft.graph.downloadUrl": "https://sanoptis.sharepoint.com/dl?t=x"},
        )
        httpx_mock.add_response(url=re.compile(r"https://sanoptis\.sharepoint\.com/dl.*"), content=b"%PDF fake")
        state, _ = teams_chats.run(client, storage, {}, self._attachment_config(), {})
        return state

    def test_reaction_only_etag_bump_reuses_attachments_without_download(self, httpx_mock: HTTPXMock, storage, client):
        """B4: a reaction bumps etag/lastModified only — attachment refs must be reused, not re-downloaded."""
        state = self._seed_with_attachment(httpx_mock, storage, client)

        _mock_chats(httpx_mock)
        bumped = _graph_msg(
            "msg-att",
            "2026-06-10T09:00:00Z",
            content="see attached",
            etag="2",
            last_modified="2026-06-12T08:00:00Z",
        )
        bumped["attachments"] = [
            {
                "contentType": "reference",
                "name": "spec.pdf",
                "contentUrl": "https://sanoptis.sharepoint.com/sites/x/spec.pdf",
            }
        ]
        httpx_mock.add_response(url=re.compile(r".*/me/chats/.*/messages.*"), json={"value": [bumped]})

        teams_chats.run(client, storage, state, self._attachment_config(), {})

        share_requests = [r for r in httpx_mock.get_requests() if "/shares/" in str(r.url)]
        assert len(share_requests) == 1, "reaction-only etag bump must not re-download attachments"
        store = load_store(storage, f"{CHAT_DIR}/messages.jsonl")
        assert store["msg-att"].etag == "2"
        assert [a["name"] for a in store["msg-att"].attachments] == ["spec.pdf"]
        assert "[spec.pdf](attachments/msg-att/spec.pdf)" in storage.read_file(f"{CHAT_DIR}/messages.md")

    def test_edit_adding_attachment_triggers_download(self, httpx_mock: HTTPXMock, storage, client):
        """B4 counter-case: a changed attachment name set must run the download path."""
        state = self._seed_with_attachment(httpx_mock, storage, client)

        _mock_chats(httpx_mock)
        edited = _graph_msg(
            "msg-att",
            "2026-06-10T09:00:00Z",
            content="see both attached",
            etag="2",
            last_modified="2026-06-12T08:00:00Z",
            edited=True,
        )
        edited["attachments"] = [
            {
                "contentType": "reference",
                "name": "spec.pdf",
                "contentUrl": "https://sanoptis.sharepoint.com/sites/x/spec.pdf",
            },
            {
                "contentType": "reference",
                "name": "extra.pdf",
                "contentUrl": "https://sanoptis.sharepoint.com/sites/x/extra.pdf",
            },
        ]
        httpx_mock.add_response(url=re.compile(r".*/me/chats/.*/messages.*"), json={"value": [edited]})
        httpx_mock.add_response(
            url=re.compile(r".*/shares/.*/driveItem.*"),
            json={"id": "di", "size": 64, "@microsoft.graph.downloadUrl": "https://sanoptis.sharepoint.com/dl?t=x"},
            is_reusable=True,
        )
        httpx_mock.add_response(
            url=re.compile(r"https://sanoptis\.sharepoint\.com/dl.*"), content=b"%PDF fake", is_reusable=True
        )

        teams_chats.run(client, storage, state, self._attachment_config(), {})

        share_requests = [r for r in httpx_mock.get_requests() if "/shares/" in str(r.url)]
        assert len(share_requests) == 3  # 1 at seed + 2 for the edited message
        store = load_store(storage, f"{CHAT_DIR}/messages.jsonl")
        assert sorted(a["name"] for a in store["msg-att"].attachments) == ["extra.pdf", "spec.pdf"]
        content = storage.read_file(f"{CHAT_DIR}/messages.md")
        assert "[spec.pdf](attachments/msg-att/spec.pdf)" in content
        assert "[extra.pdf](attachments/msg-att/extra.pdf)" in content


class TestEmptyStoreInvariant:
    """A watermark without a store file causes endless re-backfills (B5)."""

    def _backfill_system_only(self, httpx_mock: HTTPXMock, storage, client) -> dict:
        _mock_chats(httpx_mock)
        httpx_mock.add_response(
            url=re.compile(r".*/me/chats/.*/messages.*"),
            json={
                "value": [
                    _graph_msg("sys-1", "2026-06-11T09:00:00Z", content="call ended", msg_type="systemEventMessage")
                ]
            },
        )
        state, count = teams_chats.run(client, storage, {}, _config(), {})
        assert count == 0
        return state

    def test_system_only_backfill_creates_empty_store_and_no_markdown(self, httpx_mock: HTTPXMock, storage, client):
        state = self._backfill_system_only(httpx_mock, storage, client)

        assert storage.file_exists(f"{CHAT_DIR}/messages.jsonl")
        assert not storage.file_exists(f"{CHAT_DIR}/messages.md")
        assert state["watermarks"][CHAT_ID] == "2026-06-11T09:00:00Z"

    def test_second_run_is_incremental_without_re_backfill(self, httpx_mock: HTTPXMock, storage, client):
        state = self._backfill_system_only(httpx_mock, storage, client)

        _mock_chats(httpx_mock)
        httpx_mock.add_response(url=re.compile(r".*/me/chats/.*/messages.*"), json={"value": []})
        warnings: list[str] = []
        with patch.object(teams_chats.log, "warning", side_effect=lambda e, **kw: warnings.append(e)):
            teams_chats.run(client, storage, state, _config(), {})

        assert "teams_chats.store_missing_backfill" not in warnings
        last_request = [r for r in httpx_mock.get_requests() if "/messages" in str(r.url)][-1]
        assert "$filter=lastModifiedDateTime gt 2026-06-11T09:00:00Z" in unquote_plus(str(last_request.url))


class TestPerChatIsolation:
    def test_transport_error_in_hosted_content_listing_skips_chat(self, httpx_mock: HTTPXMock, storage, client):
        """A TransportError escaping the hostedContents listing must not kill the sync cycle."""
        config = TeamsChatsExtractorConfig(
            enabled=True,
            poll_interval_minutes=5,
            max_messages_per_chat=200,
            download_attachments=False,
            download_inline_images=True,
            max_attachment_size_mb=25,
            attachment_convert_extensions=[],
        )
        _mock_chats(httpx_mock)
        httpx_mock.add_response(
            url=re.compile(r".*/me/chats/.*/messages.*"),
            json={"value": [_graph_msg("m1", "2026-06-11T09:00:00Z")]},
        )
        httpx_mock.add_exception(
            httpx.ConnectError("network down"), url=re.compile(r".*/hostedContents.*"), is_reusable=True
        )

        errors: list[str] = []
        with patch.object(teams_chats.log, "error", side_effect=lambda e, **kw: errors.append(e)):
            state, count = teams_chats.run(client, storage, {}, config, {})

        assert count == 0
        assert "teams_chats.media_transport_error" in errors
        # The chat is skipped without advancing its watermark — the next cycle retries.
        assert CHAT_ID not in state["watermarks"]
        assert not storage.file_exists(f"{CHAT_DIR}/messages.jsonl")

    def test_corrupt_store_skips_chat_without_advancing_watermark(self, httpx_mock: HTTPXMock, storage, client):
        storage.write_file(f"{CHAT_DIR}/messages.jsonl", "{not valid json\n")
        state = {"watermarks": {CHAT_ID: "2026-06-10T09:00:00Z"}}
        _mock_chats(httpx_mock)
        httpx_mock.add_response(
            url=re.compile(r".*/me/chats/.*/messages.*"),
            json={"value": [_graph_msg("new-1", "2026-06-11T10:00:00Z")]},
        )

        errors: list[str] = []
        with patch.object(teams_chats.log, "error", side_effect=lambda e, **kw: errors.append(e)):
            state, count = teams_chats.run(client, storage, state, _config(), {})

        assert count == 0
        assert "teams_chats.store_corrupt" in errors
        assert state["watermarks"][CHAT_ID] == "2026-06-10T09:00:00Z"


class TestRelations:
    def test_long_participant_names_render_relations(self, httpx_mock: HTTPXMock, storage, client):
        """Participant slugs longer than 5 chars produce a Relations section."""
        httpx_mock.add_response(
            url=re.compile(r".*/me/chats\?.*"),
            json={
                "value": [
                    {
                        "id": CHAT_ID,
                        "chatType": "oneOnOne",
                        "topic": None,
                        "members": [
                            {"displayName": "Matthias Christenson"},
                            {"displayName": "Samuel Scholl"},
                        ],
                    }
                ]
            },
        )
        httpx_mock.add_response(
            url=re.compile(r".*/me/chats/.*/messages.*"),
            json={"value": [_graph_msg("m1", "2026-06-11T09:00:00Z")]},
        )

        teams_chats.run(client, storage, {}, _config(), {})

        chat_dir = f"teams-chats/{slugify('Matthias Christenson, Samuel Scholl', 80)}_{short_hash(CHAT_ID, 6)}"
        content = storage.read_file(f"{chat_dir}/messages.md")
        assert "## Relations" in content
        assert "[[contact-matthias-christenson]]" in content
        assert "[[contact-samuel-scholl]]" in content


class TestFetchTransportError:
    def test_transport_error_during_fetch_skips_chat(self, httpx_mock: HTTPXMock, storage, client):
        """A TransportError from get_pages must skip the chat without advancing watermark."""
        _mock_chats(httpx_mock)
        httpx_mock.add_exception(
            httpx.ConnectError("network down"),
            url=re.compile(r".*/me/chats/.*/messages.*"),
            is_reusable=True,
        )

        errors: list[str] = []
        with patch.object(teams_chats.log, "error", side_effect=lambda e, **kw: errors.append(e)):
            state, count = teams_chats.run(client, storage, {}, _config(), {})

        assert count == 0
        assert "teams_chats.fetch_transport_error" in errors
        assert CHAT_ID not in state["watermarks"]


class TestChatTitle:
    def test_topic_used_as_title(self, httpx_mock: HTTPXMock, storage, client):
        httpx_mock.add_response(
            url=re.compile(r".*/me/chats\?.*"),
            json={
                "value": [
                    {
                        "id": "19:topic-chat",
                        "chatType": "group",
                        "topic": "Project Alpha Planning",
                        "members": [{"displayName": "Alice"}, {"displayName": "Bob"}],
                    }
                ]
            },
        )
        httpx_mock.add_response(
            url=re.compile(r".*/me/chats/.*/messages.*"),
            json={"value": [_graph_msg("m1", "2026-06-11T09:00:00Z")]},
        )

        teams_chats.run(client, storage, {}, _config(), {})

        chat_dir = f"teams-chats/{slugify('Project Alpha Planning', 80)}_{short_hash('19:topic-chat', 6)}"
        content = storage.read_file(f"{chat_dir}/messages.md")
        assert "# Project Alpha Planning" in content
        assert "teams-group" in content


class TestConvertedAttachmentLink:
    def test_converted_path_renders_text_link(self, httpx_mock: HTTPXMock, storage, client):
        """An attachment with a non-None converted_path renders both raw and (text) links."""
        config = TeamsChatsExtractorConfig(
            enabled=True,
            poll_interval_minutes=5,
            max_messages_per_chat=200,
            download_attachments=True,
            download_inline_images=False,
            max_attachment_size_mb=100,
            attachment_convert_extensions=[".docx"],
        )
        content_url = "https://sanoptis.sharepoint.com/sites/x/report.docx"

        _mock_chats(httpx_mock)
        msg = _graph_msg("msg-conv", "2026-06-10T09:00:00Z", content="see report")
        msg["attachments"] = [{"contentType": "reference", "name": "report.docx", "contentUrl": content_url}]
        httpx_mock.add_response(url=re.compile(r".*/me/chats/.*/messages.*"), json={"value": [msg]})
        httpx_mock.add_response(
            url=re.compile(r".*/shares/.*/driveItem.*"),
            json={
                "id": "di",
                "size": 64,
                "@microsoft.graph.downloadUrl": "https://sanoptis.sharepoint.com/dl?t=x",
            },
        )
        httpx_mock.add_response(
            url=re.compile(r"https://sanoptis\.sharepoint\.com/dl.*"),
            content=b"PK\x03\x04fake docx",
        )

        with patch("m365_extract.extractors._attachment_helpers.convert_document", return_value="# Converted"):
            teams_chats.run(client, storage, {}, config, {})

        content = storage.read_file(f"{CHAT_DIR}/messages.md")
        assert "[report.docx](attachments/msg-conv/report.docx)" in content
        assert "[report.docx (text)](attachments_converted/msg-conv/report.docx.md)" in content
