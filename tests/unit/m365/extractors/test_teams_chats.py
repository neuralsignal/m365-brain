"""Tests for the merge-based Teams chat extractor (Teams Sync v2)."""

from __future__ import annotations

import re
from unittest.mock import patch
from urllib.parse import unquote_plus

import httpx
import pytest
from pytest_httpx import HTTPXMock

from m365_brain.config import GraphConfig, TeamsChatsExtractorConfig
from m365_brain.config.index import RelationConfig
from m365_brain.m365.client import GRAPH_BASE_URL, GraphClient
from m365_brain.m365.extractors import teams_chats
from m365_brain.m365.extractors._message_store import load_store
from m365_brain.m365.frontmatter.teams import PARTICIPANT
from m365_brain.m365.markdown_writer import short_hash, slugify
from m365_brain.parsers.relations import parse_relations
from m365_brain.storage.local import LocalBackend
from m365_brain.vault.paths import VaultPaths

CHAT_ID = "19:chat-1"
TOMBSTONE_ID = "19:meeting_tombstone@thread.v2"


def _chat_dir(paths: VaultPaths, title: str = "Alice, Bob", chat_id: str = CHAT_ID) -> str:
    """The chat directory, resolved through the layout config the extractor reads."""
    return paths.inbox_item("teams_chats", f"{slugify(title, 80)}_{short_hash(chat_id, 6)}")


def _md(paths: VaultPaths, **kw) -> str:
    return paths.conversation_file(_chat_dir(paths, **kw))


def _store(paths: VaultPaths, **kw) -> str:
    return paths.conversation_store(_chat_dir(paths, **kw))


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


def _tombstone_chat() -> dict:
    """A chat the account has lost access to, exactly as `/me/chats` reports it.

    Every substantive field is null or `DateTime.MinValue` while the entity
    itself still answers 200; only its sub-collections 403. Copied from a live
    probe of two such chats, one of which was caught making the transition six
    minutes after its meeting ended.
    """
    return {
        "id": TOMBSTONE_ID,
        "topic": "Introduction call",
        "chatType": "unknownFutureValue",
        "createdDateTime": "0001-01-01T00:00:00Z",
        "tenantId": None,
        "webUrl": None,
        "members": [],
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
    def test_backfill_paginates_to_exhaustion(self, httpx_mock: HTTPXMock, storage, client, ctx):
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

        state, count = teams_chats.run(client, storage, {}, _config(), ctx)

        assert count == 1
        content = storage.read_file(_md(ctx.paths))
        assert "newer" in content and "older" in content
        assert len(load_store(storage, _store(ctx.paths))) == 2

    def test_backfill_request_has_no_filter(self, httpx_mock: HTTPXMock, storage, client, ctx):
        _mock_chats(httpx_mock)
        httpx_mock.add_response(
            url=re.compile(r".*/me/chats/.*/messages.*"),
            json={"value": [_graph_msg("m1", "2026-06-11T09:00:00Z")]},
        )

        teams_chats.run(client, storage, {}, _config(), ctx)

        msg_request = [r for r in httpx_mock.get_requests() if "/messages" in str(r.url)][0]
        assert "$filter" not in unquote_plus(str(msg_request.url))

    def test_watermark_set_to_max_last_modified(self, httpx_mock: HTTPXMock, storage, client, ctx):
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

        state, _ = teams_chats.run(client, storage, {}, _config(), ctx)

        assert state["watermarks"][CHAT_ID] == "2026-06-11T12:00:00Z"

    def test_cap_hit_sets_history_complete_false(self, httpx_mock: HTTPXMock, storage, client, ctx):
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

        teams_chats.run(client, storage, {}, _config(max_messages=2), ctx)

        content = storage.read_file(_md(ctx.paths))
        assert "history_complete: false" in content
        assert "message_limit_reached" not in content

    def test_count_at_cap_without_next_link_is_complete(self, httpx_mock: HTTPXMock, storage, client, ctx):
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

        teams_chats.run(client, storage, {}, _config(max_messages=2), ctx)

        content = storage.read_file(_md(ctx.paths))
        assert "history_complete: true" in content

    def test_full_backfill_sets_history_complete_true(self, httpx_mock: HTTPXMock, storage, client, ctx):
        _mock_chats(httpx_mock)
        httpx_mock.add_response(
            url=re.compile(r".*/me/chats/.*/messages.*"),
            json={"value": [_graph_msg("m1", "2026-06-11T09:00:00Z")]},
        )

        teams_chats.run(client, storage, {}, _config(), ctx)

        content = storage.read_file(_md(ctx.paths))
        assert "history_complete: true" in content
        assert "message_count: 1" in content


class TestIncremental:
    def _seed(self, httpx_mock: HTTPXMock, storage, client, ctx) -> dict:
        """Backfill one old message, returning the resulting state."""
        _mock_chats(httpx_mock)
        httpx_mock.add_response(
            url=re.compile(r".*/me/chats/.*/messages.*"),
            json={"value": [_graph_msg("old-1", "2026-06-10T09:00:00Z", content="old history")]},
        )
        state, _ = teams_chats.run(client, storage, {}, _config(), ctx)
        return state

    def test_incremental_pairs_filter_with_orderby_on_last_modified(self, httpx_mock: HTTPXMock, storage, client, ctx):
        """The central v1 bug: $filter is silently ignored unless $orderby targets the same property."""
        state = self._seed(httpx_mock, storage, client, ctx)

        _mock_chats(httpx_mock)
        httpx_mock.add_response(
            url=re.compile(r".*/me/chats/.*/messages.*"),
            json={"value": [_graph_msg("new-1", "2026-06-11T10:00:00Z", content="brand new")]},
        )
        teams_chats.run(client, storage, state, _config(), ctx)

        incremental_request = [r for r in httpx_mock.get_requests() if "/messages" in str(r.url)][-1]
        url = unquote_plus(str(incremental_request.url))
        assert "$orderby=lastModifiedDateTime desc" in url
        assert "$filter=lastModifiedDateTime gt 2026-06-10T09:00:00Z" in url

    def test_merge_preserves_messages_absent_from_fetch(self, httpx_mock: HTTPXMock, storage, client, ctx):
        """The v1 data-loss regression: old messages must survive an incremental fetch."""
        state = self._seed(httpx_mock, storage, client, ctx)

        _mock_chats(httpx_mock)
        httpx_mock.add_response(
            url=re.compile(r".*/me/chats/.*/messages.*"),
            json={"value": [_graph_msg("new-1", "2026-06-11T10:00:00Z", content="brand new")]},
        )
        teams_chats.run(client, storage, state, _config(), ctx)

        content = storage.read_file(_md(ctx.paths))
        assert "old history" in content
        assert "brand new" in content
        assert len(load_store(storage, _store(ctx.paths))) == 2

    def test_no_write_when_nothing_changed(self, httpx_mock: HTTPXMock, storage, client, ctx):
        state = self._seed(httpx_mock, storage, client, ctx)
        before = storage.read_file(_md(ctx.paths))

        _mock_chats(httpx_mock)
        httpx_mock.add_response(url=re.compile(r".*/me/chats/.*/messages.*"), json={"value": []})

        writes: list[str] = []
        original_write = storage.write_file
        storage.write_file = lambda path, content: writes.append(path) or original_write(path, content)
        _, count = teams_chats.run(client, storage, state, _config(), ctx)

        assert count == 0
        assert writes == []
        assert storage.read_file(_md(ctx.paths)) == before

    def test_refetched_unchanged_etag_does_not_rewrite(self, httpx_mock: HTTPXMock, storage, client, ctx):
        state = self._seed(httpx_mock, storage, client, ctx)

        _mock_chats(httpx_mock)
        httpx_mock.add_response(
            url=re.compile(r".*/me/chats/.*/messages.*"),
            json={"value": [_graph_msg("old-1", "2026-06-10T09:00:00Z", content="old history")]},
        )
        writes: list[str] = []
        original_write = storage.write_file
        storage.write_file = lambda path, content: writes.append(path) or original_write(path, content)
        _, count = teams_chats.run(client, storage, state, _config(), ctx)

        assert count == 0
        assert writes == []

    def test_edited_message_replaces_content(self, httpx_mock: HTTPXMock, storage, client, ctx):
        state = self._seed(httpx_mock, storage, client, ctx)

        _mock_chats(httpx_mock)
        httpx_mock.add_response(
            url=re.compile(r".*/me/chats/.*/messages.*"),
            json={"value": [_graph_msg("old-1", "2026-06-10T09:00:00Z", content="corrected", etag="2", edited=True)]},
        )
        teams_chats.run(client, storage, state, _config(), ctx)

        content = storage.read_file(_md(ctx.paths))
        assert "corrected" in content
        assert "old history" not in content
        assert "*(edited)*" in content

    def test_incremental_uses_global_page_bound_not_backfill_cap(self, httpx_mock: HTTPXMock, storage, client, ctx):
        """A 2-page incremental window must be fetched fully even when the backfill cap is one page."""
        config = _config(max_messages=50)  # backfill budget: exactly one page
        _mock_chats(httpx_mock)
        httpx_mock.add_response(
            url=re.compile(r".*/me/chats/.*/messages.*"),
            json={"value": [_graph_msg("old-1", "2026-06-10T09:00:00Z")]},
        )
        state, _ = teams_chats.run(client, storage, {}, config, ctx)

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

        teams_chats.run(client, storage, state, config, ctx)

        store = load_store(storage, _store(ctx.paths))
        assert {"old-1", "new-1", "new-2"} <= set(store)
        assert state["watermarks"][CHAT_ID] == "2026-06-11T11:00:00Z"

    def test_incremental_truncation_at_global_bound_does_not_advance_watermark(
        self, httpx_mock: HTTPXMock, storage, graph_config, ctx
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
            state = self._seed(httpx_mock, storage, client, ctx)
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
                teams_chats.run(client, storage, state, _config(), ctx)

            assert "teams_chats.incremental_truncated" in errors
            assert state["watermarks"][CHAT_ID] == "2026-06-10T09:00:00Z"
            # Fetched messages are still merged — only the watermark is held back.
            assert "new-1" in load_store(storage, _store(ctx.paths))
        finally:
            client.close()

    def test_unknown_history_completeness_renders_false(self, httpx_mock: HTTPXMock, storage, client, ctx):
        """Pessimistic default: a missing history_complete state key must render as false."""
        state = self._seed(httpx_mock, storage, client, ctx)
        state["history_complete"].pop(CHAT_ID)

        _mock_chats(httpx_mock)
        httpx_mock.add_response(
            url=re.compile(r".*/me/chats/.*/messages.*"),
            json={"value": [_graph_msg("new-1", "2026-06-11T10:00:00Z")]},
        )
        teams_chats.run(client, storage, state, _config(), ctx)

        content = storage.read_file(_md(ctx.paths))
        assert "history_complete: false" in content

    def test_missing_store_with_watermark_triggers_backfill(self, httpx_mock: HTTPXMock, storage, client, ctx):
        """Manual store deletion drops the watermark — the store is the source of truth."""
        state = {"watermarks": {CHAT_ID: "2026-06-10T09:00:00Z"}}
        _mock_chats(httpx_mock)
        httpx_mock.add_response(
            url=re.compile(r".*/me/chats/.*/messages.*"),
            json={"value": [_graph_msg("m1", "2026-06-11T09:00:00Z")]},
        )

        teams_chats.run(client, storage, state, _config(), ctx)

        msg_request = [r for r in httpx_mock.get_requests() if "/messages" in str(r.url)][0]
        assert "$filter" not in unquote_plus(str(msg_request.url))


class TestFiltering:
    def test_only_message_type_kept(self, httpx_mock: HTTPXMock, storage, client, ctx):
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

        teams_chats.run(client, storage, {}, _config(), ctx)

        content = storage.read_file(_md(ctx.paths))
        assert "real message" in content
        assert "system event" not in content
        assert "unknown stuff" not in content

    def test_chat_with_no_messages_not_written(self, httpx_mock: HTTPXMock, storage, client, ctx):
        _mock_chats(httpx_mock)
        httpx_mock.add_response(url=re.compile(r".*/me/chats/.*/messages.*"), json={"value": []})

        state, count = teams_chats.run(client, storage, {}, _config(), ctx)

        assert count == 0
        assert storage.list_files(ctx.paths.inbox_root("teams_chats")) == []

    def test_fetch_failure_continues_to_next_chat(self, httpx_mock: HTTPXMock, storage, client, ctx):
        httpx_mock.add_response(
            url=re.compile(r".*/me/chats\?.*"),
            json=_chats_response(),
        )
        httpx_mock.add_response(url=re.compile(r".*/me/chats/.*/messages.*"), status_code=403)

        state, count = teams_chats.run(client, storage, {}, _config(), ctx)

        assert count == 0

    def test_transport_error_on_fetch_skips_chat(self, httpx_mock: HTTPXMock, storage, client, ctx):
        _mock_chats(httpx_mock)
        httpx_mock.add_exception(
            httpx.ConnectError("network down"), url=re.compile(r".*/me/chats/.*/messages.*"), is_reusable=True
        )

        state, count = teams_chats.run(client, storage, {}, _config(), ctx)

        assert count == 0
        assert CHAT_ID not in state["watermarks"]


class TestTombstoneChats:
    """Chats the account has lost membership of, which `/me/chats` still lists."""

    def test_a_tombstone_chat_is_never_asked_for_messages(self, httpx_mock: HTTPXMock, storage, client, ctx):
        """Asking costs an error-level `graph.request_failed` every cycle, forever.

        That line comes from the transport, which logs every non-retryable
        response before raising, so nothing the extractor logs can soften it.
        Not making the request is the only thing that removes it.
        """
        httpx_mock.add_response(url=re.compile(r".*/me/chats\?.*"), json={"value": [_tombstone_chat()]})

        _, count = teams_chats.run(client, storage, {}, _config(), ctx)

        assert count == 0
        assert [str(r.url) for r in httpx_mock.get_requests() if "/messages" in str(r.url)] == []

    def test_a_readable_chat_beside_a_tombstone_is_still_fetched(self, httpx_mock: HTTPXMock, storage, client, ctx):
        """The filter has to discriminate, not merely suppress."""
        httpx_mock.add_response(
            url=re.compile(r".*/me/chats\?.*"),
            json={"value": [_tombstone_chat(), *_chats_response()["value"]]},
        )
        httpx_mock.add_response(
            url=re.compile(r".*/me/chats/.*/messages.*"),
            json={"value": [_graph_msg("m1", "2026-06-11T09:00:00Z")]},
        )

        _, count = teams_chats.run(client, storage, {}, _config(), ctx)

        assert count == 1
        requested = [str(r.url) for r in httpx_mock.get_requests() if "/messages" in str(r.url)]
        assert len(requested) == 1
        assert "chat-1" in requested[0]
        assert "tombstone" not in requested[0]


class TestRendering:
    def test_new_standard_format(self, httpx_mock: HTTPXMock, storage, client, ctx):
        _mock_chats(httpx_mock)
        httpx_mock.add_response(
            url=re.compile(r".*/me/chats/.*/messages.*"),
            json={
                "value": [
                    _graph_msg("m2", "2026-06-11T10:20:00Z", sender="Jordan Kim", content="reply", edited=True),
                    _graph_msg("m1", "2026-06-11T09:42:00Z", sender="Alex Doe", content="hi"),
                ]
            },
        )

        teams_chats.run(client, storage, {}, _config(), ctx)

        content = storage.read_file(_md(ctx.paths))
        assert "## 2026-06-11" in content
        assert "### 09:42 — Alex Doe" in content
        assert "### 10:20 — Jordan Kim *(edited)*" in content
        assert "## Messages" in content
        assert "# Alice, Bob" in content

    def test_skeleton_sections_present(self, httpx_mock: HTTPXMock, storage, client, ctx):
        _mock_chats(httpx_mock)
        httpx_mock.add_response(
            url=re.compile(r".*/me/chats/.*/messages.*"),
            json={"value": [_graph_msg("m1", "2026-06-11T09:42:00Z")]},
        )

        teams_chats.run(client, storage, {}, _config(), ctx)

        content = storage.read_file(_md(ctx.paths))
        assert "## Observations" in content
        assert "- [message_count] 1" in content
        assert content.index("## Observations") < content.index("## Messages")

    def test_relations_section_rendered_for_participants(self, httpx_mock: HTTPXMock, storage, client, ctx):
        chat_id = "19:relations-chat"
        httpx_mock.add_response(
            url=re.compile(r".*/me/chats\?.*"),
            json={
                "value": [
                    {
                        "id": chat_id,
                        "chatType": "oneOnOne",
                        "topic": None,
                        "members": [
                            {"displayName": "Alex Doe"},
                            {"displayName": "Jordan Kim"},
                        ],
                    }
                ]
            },
        )
        httpx_mock.add_response(
            url=re.compile(r".*/me/chats/.*/messages.*"),
            json={"value": [_graph_msg("m1", "2026-06-11T09:42:00Z")]},
        )

        teams_chats.run(client, storage, {}, _config(), ctx)

        content = storage.read_file(_md(ctx.paths, title="Alex Doe, Jordan Kim", chat_id=chat_id))
        assert "## Relations" in content
        assert "- participant [[Alex Doe]]" in content
        assert "- participant [[Jordan Kim]]" in content

    def test_deleted_message_renders_tombstone(self, httpx_mock: HTTPXMock, storage, client, ctx):
        _mock_chats(httpx_mock)
        httpx_mock.add_response(
            url=re.compile(r".*/me/chats/.*/messages.*"),
            json={"value": [_graph_msg("m1", "2026-06-11T09:42:00Z", content="", deleted=True)]},
        )

        teams_chats.run(client, storage, {}, _config(), ctx)

        content = storage.read_file(_md(ctx.paths))
        assert "*(deleted)*" in content
        assert "*Message deleted.*" in content


class TestFetchTransportError:
    def test_transport_error_during_fetch_skips_chat(self, httpx_mock: HTTPXMock, storage, client, ctx):
        """An httpx.TransportError from the message fetch must skip the chat gracefully."""
        _mock_chats(httpx_mock)
        httpx_mock.add_exception(
            httpx.ConnectError("network down"), url=re.compile(r".*/me/chats/.*/messages.*"), is_reusable=True
        )

        errors: list[str] = []
        with patch.object(teams_chats.log, "error", side_effect=lambda e, **kw: errors.append(e)):
            state, count = teams_chats.run(client, storage, {}, _config(), ctx)

        assert count == 0
        assert "teams_chats.fetch_transport_error" in errors
        assert CHAT_ID not in state["watermarks"]


class TestAttachments:
    def test_attachment_links_survive_re_render(self, httpx_mock: HTTPXMock, storage, client, ctx):
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
        content_url = "https://contoso.sharepoint.com/sites/x/spec.pdf"

        _mock_chats(httpx_mock)
        msg = _graph_msg("msg-att", "2026-06-10T09:00:00Z", content="see attached")
        msg["attachments"] = [{"contentType": "reference", "name": "spec.pdf", "contentUrl": content_url}]
        httpx_mock.add_response(url=re.compile(r".*/me/chats/.*/messages.*"), json={"value": [msg]})
        httpx_mock.add_response(
            url=re.compile(r".*/shares/.*/driveItem.*"),
            json={"id": "di", "size": 64, "@microsoft.graph.downloadUrl": "https://contoso.sharepoint.com/dl?t=x"},
        )
        httpx_mock.add_response(url=re.compile(r"https://contoso\.sharepoint\.com/dl.*"), content=b"%PDF fake")

        state, _ = teams_chats.run(client, storage, {}, config, ctx)
        content = storage.read_file(_md(ctx.paths))
        assert "[spec.pdf](attachments/msg-att/spec.pdf)" in content

        # Incremental run fetching only a new message must keep the old link.
        _mock_chats(httpx_mock)
        httpx_mock.add_response(
            url=re.compile(r".*/me/chats/.*/messages.*"),
            json={"value": [_graph_msg("msg-new", "2026-06-11T10:00:00Z", content="follow-up")]},
        )
        teams_chats.run(client, storage, state, config, ctx)

        content = storage.read_file(_md(ctx.paths))
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

    def _seed_with_attachment(self, httpx_mock: HTTPXMock, storage, client, ctx) -> dict:
        """Backfill one message carrying spec.pdf; returns the resulting state."""
        content_url = "https://contoso.sharepoint.com/sites/x/spec.pdf"
        _mock_chats(httpx_mock)
        msg = _graph_msg("msg-att", "2026-06-10T09:00:00Z", content="see attached")
        msg["attachments"] = [{"contentType": "reference", "name": "spec.pdf", "contentUrl": content_url}]
        httpx_mock.add_response(url=re.compile(r".*/me/chats/.*/messages.*"), json={"value": [msg]})
        httpx_mock.add_response(
            url=re.compile(r".*/shares/.*/driveItem.*"),
            json={"id": "di", "size": 64, "@microsoft.graph.downloadUrl": "https://contoso.sharepoint.com/dl?t=x"},
        )
        httpx_mock.add_response(url=re.compile(r"https://contoso\.sharepoint\.com/dl.*"), content=b"%PDF fake")
        state, _ = teams_chats.run(client, storage, {}, self._attachment_config(), ctx)
        return state

    def test_reaction_only_etag_bump_reuses_attachments_without_download(
        self, httpx_mock: HTTPXMock, storage, client, ctx
    ):
        """B4: a reaction bumps etag/lastModified only — attachment refs must be reused, not re-downloaded."""
        state = self._seed_with_attachment(httpx_mock, storage, client, ctx)

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
                "contentUrl": "https://contoso.sharepoint.com/sites/x/spec.pdf",
            }
        ]
        httpx_mock.add_response(url=re.compile(r".*/me/chats/.*/messages.*"), json={"value": [bumped]})

        teams_chats.run(client, storage, state, self._attachment_config(), ctx)

        share_requests = [r for r in httpx_mock.get_requests() if "/shares/" in str(r.url)]
        assert len(share_requests) == 1, "reaction-only etag bump must not re-download attachments"
        store = load_store(storage, _store(ctx.paths))
        assert store["msg-att"].etag == "2"
        assert [a["name"] for a in store["msg-att"].attachments] == ["spec.pdf"]
        assert "[spec.pdf](attachments/msg-att/spec.pdf)" in storage.read_file(_md(ctx.paths))

    def test_edit_adding_attachment_triggers_download(self, httpx_mock: HTTPXMock, storage, client, ctx):
        """B4 counter-case: a changed attachment name set must run the download path."""
        state = self._seed_with_attachment(httpx_mock, storage, client, ctx)

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
                "contentUrl": "https://contoso.sharepoint.com/sites/x/spec.pdf",
            },
            {
                "contentType": "reference",
                "name": "extra.pdf",
                "contentUrl": "https://contoso.sharepoint.com/sites/x/extra.pdf",
            },
        ]
        httpx_mock.add_response(url=re.compile(r".*/me/chats/.*/messages.*"), json={"value": [edited]})
        httpx_mock.add_response(
            url=re.compile(r".*/shares/.*/driveItem.*"),
            json={"id": "di", "size": 64, "@microsoft.graph.downloadUrl": "https://contoso.sharepoint.com/dl?t=x"},
            is_reusable=True,
        )
        httpx_mock.add_response(
            url=re.compile(r"https://contoso\.sharepoint\.com/dl.*"), content=b"%PDF fake", is_reusable=True
        )

        teams_chats.run(client, storage, state, self._attachment_config(), ctx)

        share_requests = [r for r in httpx_mock.get_requests() if "/shares/" in str(r.url)]
        assert len(share_requests) == 3  # 1 at seed + 2 for the edited message
        store = load_store(storage, _store(ctx.paths))
        assert sorted(a["name"] for a in store["msg-att"].attachments) == ["extra.pdf", "spec.pdf"]
        content = storage.read_file(_md(ctx.paths))
        assert "[spec.pdf](attachments/msg-att/spec.pdf)" in content
        assert "[extra.pdf](attachments/msg-att/extra.pdf)" in content


class TestEmptyStoreInvariant:
    """A watermark without a store file causes endless re-backfills (B5)."""

    def _backfill_system_only(self, httpx_mock: HTTPXMock, storage, client, ctx) -> dict:
        _mock_chats(httpx_mock)
        httpx_mock.add_response(
            url=re.compile(r".*/me/chats/.*/messages.*"),
            json={
                "value": [
                    _graph_msg("sys-1", "2026-06-11T09:00:00Z", content="call ended", msg_type="systemEventMessage")
                ]
            },
        )
        state, count = teams_chats.run(client, storage, {}, _config(), ctx)
        assert count == 0
        return state

    def test_system_only_backfill_creates_empty_store_and_no_markdown(
        self, httpx_mock: HTTPXMock, storage, client, ctx
    ):
        state = self._backfill_system_only(httpx_mock, storage, client, ctx)

        assert storage.file_exists(_store(ctx.paths))
        assert not storage.file_exists(_md(ctx.paths))
        assert state["watermarks"][CHAT_ID] == "2026-06-11T09:00:00Z"

    def test_second_run_is_incremental_without_re_backfill(self, httpx_mock: HTTPXMock, storage, client, ctx):
        state = self._backfill_system_only(httpx_mock, storage, client, ctx)

        _mock_chats(httpx_mock)
        httpx_mock.add_response(url=re.compile(r".*/me/chats/.*/messages.*"), json={"value": []})
        warnings: list[str] = []
        with patch.object(teams_chats.log, "warning", side_effect=lambda e, **kw: warnings.append(e)):
            teams_chats.run(client, storage, state, _config(), ctx)

        assert "teams_chats.store_missing_backfill" not in warnings
        last_request = [r for r in httpx_mock.get_requests() if "/messages" in str(r.url)][-1]
        assert "$filter=lastModifiedDateTime gt 2026-06-11T09:00:00Z" in unquote_plus(str(last_request.url))


class TestPerChatIsolation:
    def test_transport_error_in_hosted_content_listing_skips_chat(self, httpx_mock: HTTPXMock, storage, client, ctx):
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
            state, count = teams_chats.run(client, storage, {}, config, ctx)

        assert count == 0
        assert "teams_chats.media_transport_error" in errors
        # The chat is skipped without advancing its watermark — the next cycle retries.
        assert CHAT_ID not in state["watermarks"]
        assert not storage.file_exists(_store(ctx.paths))

    def test_transport_error_in_message_fetch_skips_chat(self, httpx_mock: HTTPXMock, storage, client, ctx):
        """A TransportError during get_pages (message listing) skips the chat."""
        _mock_chats(httpx_mock)
        httpx_mock.add_exception(
            httpx.ConnectError("network down"), url=re.compile(r".*/me/chats/.*/messages.*"), is_reusable=True
        )

        errors: list[str] = []
        with patch.object(teams_chats.log, "error", side_effect=lambda e, **kw: errors.append(e)):
            state, count = teams_chats.run(client, storage, {}, _config(), ctx)

        assert count == 0
        assert "teams_chats.fetch_transport_error" in errors
        assert CHAT_ID not in state["watermarks"]
        assert not storage.file_exists(_store(ctx.paths))

    def test_corrupt_store_skips_chat_without_advancing_watermark(self, httpx_mock: HTTPXMock, storage, client, ctx):
        storage.write_file(_store(ctx.paths), "{not valid json\n")
        state = {"watermarks": {CHAT_ID: "2026-06-10T09:00:00Z"}}
        _mock_chats(httpx_mock)
        httpx_mock.add_response(
            url=re.compile(r".*/me/chats/.*/messages.*"),
            json={"value": [_graph_msg("new-1", "2026-06-11T10:00:00Z")]},
        )

        errors: list[str] = []
        with patch.object(teams_chats.log, "error", side_effect=lambda e, **kw: errors.append(e)):
            state, count = teams_chats.run(client, storage, state, _config(), ctx)

        assert count == 0
        assert "teams_chats.store_corrupt" in errors
        assert state["watermarks"][CHAT_ID] == "2026-06-10T09:00:00Z"


class TestConvertedAttachmentRendering:
    def test_converted_attachment_renders_text_link(self, httpx_mock: HTTPXMock, storage, client, ctx):
        """When an attachment has a converted_path, both the raw and (text) links are rendered."""
        config = TeamsChatsExtractorConfig(
            enabled=True,
            poll_interval_minutes=5,
            max_messages_per_chat=200,
            download_attachments=True,
            download_inline_images=False,
            max_attachment_size_mb=100,
            attachment_convert_extensions=[".pdf"],
        )
        content_url = "https://example.sharepoint.com/sites/x/report.pdf"
        _mock_chats(httpx_mock)
        msg = _graph_msg("msg-conv", "2026-06-10T09:00:00Z", content="see converted")
        msg["attachments"] = [{"contentType": "reference", "name": "report.pdf", "contentUrl": content_url}]
        httpx_mock.add_response(url=re.compile(r".*/me/chats/.*/messages.*"), json={"value": [msg]})
        httpx_mock.add_response(
            url=re.compile(r".*/shares/.*/driveItem.*"),
            json={"id": "di", "size": 64, "@microsoft.graph.downloadUrl": "https://example.sharepoint.com/dl?t=x"},
        )
        httpx_mock.add_response(url=re.compile(r"https://example\.sharepoint\.com/dl.*"), content=b"%PDF fake")

        with patch("m365_brain.m365.extractors._teams_attachment_helpers.convert_and_store", return_value=True):
            teams_chats.run(client, storage, {}, config, ctx)

        content = storage.read_file(_md(ctx.paths))
        assert "[report.pdf](attachments/msg-conv/report.pdf)" in content
        assert "[report.pdf (text)](attachments_converted/msg-conv/report.pdf.md)" in content


class TestInlineImageDownload:
    def test_inline_images_downloaded_when_enabled(self, httpx_mock: HTTPXMock, storage, client, ctx):
        """When download_inline_images=True, hosted images are downloaded and body src is rewritten."""
        config = TeamsChatsExtractorConfig(
            enabled=True,
            poll_interval_minutes=5,
            max_messages_per_chat=200,
            download_attachments=False,
            download_inline_images=True,
            max_attachment_size_mb=25,
            attachment_convert_extensions=[],
        )
        hid = "aWQxMjM"
        _mock_chats(httpx_mock)
        html_body = (
            f"<p>Look at this:</p>"
            f'<img src="https://graph.microsoft.com/v1.0/chats/{CHAT_ID}/messages/m1'
            f'/hostedContents/{hid}/$value" alt="screenshot" />'
        )
        msg = _graph_msg("m1", "2026-06-11T09:00:00Z", content=html_body)
        msg["body"]["contentType"] = "html"
        httpx_mock.add_response(url=re.compile(r".*/me/chats/.*/messages\?.*"), json={"value": [msg]})
        httpx_mock.add_response(
            url=re.compile(r".*/hostedContents\?.*"),
            json={"value": [{"id": hid}]},
        )
        httpx_mock.add_response(
            url=re.compile(r".*/hostedContents/.*/\$value"),
            content=b"\x89PNG fake image bytes",
            headers={"Content-Type": "image/png"},
        )

        state, count = teams_chats.run(client, storage, {}, config, ctx)

        assert count == 1
        content = storage.read_file(_md(ctx.paths))
        assert "attachments/m1/inline_0.png" in content
        assert storage.file_exists(ctx.paths.attachment(_chat_dir(ctx.paths), "m1", "inline_0.png"))


class TestRelations:
    def test_written_file_parses_into_one_edge_per_participant(self, httpx_mock: HTTPXMock, storage, client, ctx):
        """End to end: what the extractor wrote, read back by the real relation parser.

        `ops tiers` counts a chat's counterparties off these edges, so a
        participant that survives as prose or as a joined observation is a
        counterparty the report cannot see.
        """
        httpx_mock.add_response(
            url=re.compile(r".*/me/chats\?.*"),
            json={
                "value": [
                    {
                        "id": CHAT_ID,
                        "chatType": "oneOnOne",
                        "topic": None,
                        "members": [
                            {"displayName": "Alex Doe"},
                            {"displayName": "Jordan Kim"},
                        ],
                    }
                ]
            },
        )
        httpx_mock.add_response(
            url=re.compile(r".*/me/chats/.*/messages.*"),
            json={"value": [_graph_msg("m1", "2026-06-11T09:00:00Z")]},
        )

        teams_chats.run(client, storage, {}, _config(), ctx)

        content = storage.read_file(_md(ctx.paths, title="Alex Doe, Jordan Kim"))
        parsed = parse_relations(content, RelationConfig(explicit_default_type="relates_to", inline_type="links_to"))

        assert [(edge.relation_type, edge.to_name) for edge in parsed] == [
            (PARTICIPANT, "Alex Doe"),
            (PARTICIPANT, "Jordan Kim"),
        ]
        assert "- [participants]" not in content, "a joined observation is one counterparty, not two"


class TestChatTitle:
    def test_topic_used_as_title(self, httpx_mock: HTTPXMock, storage, client, ctx):
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

        teams_chats.run(client, storage, {}, _config(), ctx)

        content = storage.read_file(_md(ctx.paths, title="Project Alpha Planning", chat_id="19:topic-chat"))
        assert "# Project Alpha Planning" in content
        assert "teams-group" in content


class TestBuildChatFetchParams:
    def test_backfill_uses_cap_from_config(self):
        config = _config(max_messages=200)
        params, max_pages = teams_chats._build_chat_fetch_params(None, config, 10)
        assert "$filter" not in params
        assert "$orderby" not in params
        assert params["$top"] == "50"
        assert max_pages == 4

    def test_incremental_uses_filter_and_client_max_pages(self):
        config = _config(max_messages=200)
        params, max_pages = teams_chats._build_chat_fetch_params("2026-06-10T09:00:00Z", config, 10)
        assert params["$filter"] == "lastModifiedDateTime gt 2026-06-10T09:00:00Z"
        assert params["$orderby"] == "lastModifiedDateTime desc"
        assert max_pages == 10

    def test_small_backfill_cap_rounds_up(self):
        config = _config(max_messages=1)
        _, max_pages = teams_chats._build_chat_fetch_params(None, config, 10)
        assert max_pages == 1


class TestAdvanceChatWatermark:
    def test_advance_sets_max_last_modified(self):
        state: dict = {"watermarks": {}}
        fetched_raw = [
            {"lastModifiedDateTime": "2026-06-11T10:00:00Z", "createdDateTime": "2026-06-11T09:00:00Z"},
            {"lastModifiedDateTime": "2026-06-11T12:00:00Z", "createdDateTime": "2026-06-11T08:00:00Z"},
        ]
        teams_chats._advance_chat_watermark(state, "chat-1", fetched_raw, None, True)
        assert state["watermarks"]["chat-1"] == "2026-06-11T12:00:00Z"

    def test_no_advance_when_flag_is_false(self):
        state: dict = {"watermarks": {"chat-1": "2026-06-10T09:00:00Z"}}
        fetched_raw = [{"lastModifiedDateTime": "2026-06-11T12:00:00Z", "createdDateTime": "2026-06-11T08:00:00Z"}]
        teams_chats._advance_chat_watermark(state, "chat-1", fetched_raw, "2026-06-10T09:00:00Z", False)
        assert state["watermarks"]["chat-1"] == "2026-06-10T09:00:00Z"

    def test_advance_keeps_max_of_old_and_new(self):
        state: dict = {"watermarks": {"chat-1": "2026-06-12T00:00:00Z"}}
        fetched_raw = [{"lastModifiedDateTime": "2026-06-11T10:00:00Z", "createdDateTime": "2026-06-11T09:00:00Z"}]
        teams_chats._advance_chat_watermark(state, "chat-1", fetched_raw, "2026-06-12T00:00:00Z", True)
        assert state["watermarks"]["chat-1"] == "2026-06-12T00:00:00Z"

    def test_falls_back_to_created_when_last_modified_missing(self):
        state: dict = {"watermarks": {}}
        fetched_raw = [{"createdDateTime": "2026-06-11T09:00:00Z"}]
        teams_chats._advance_chat_watermark(state, "chat-1", fetched_raw, None, True)
        assert state["watermarks"]["chat-1"] == "2026-06-11T09:00:00Z"


class TestConvertedAttachmentLink:
    def test_converted_path_renders_text_link(self, httpx_mock: HTTPXMock, storage, client, ctx):
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
        content_url = "https://contoso.sharepoint.com/sites/x/report.docx"

        _mock_chats(httpx_mock)
        msg = _graph_msg("msg-conv", "2026-06-10T09:00:00Z", content="see report")
        msg["attachments"] = [{"contentType": "reference", "name": "report.docx", "contentUrl": content_url}]
        httpx_mock.add_response(url=re.compile(r".*/me/chats/.*/messages.*"), json={"value": [msg]})
        httpx_mock.add_response(
            url=re.compile(r".*/shares/.*/driveItem.*"),
            json={
                "id": "di",
                "size": 64,
                "@microsoft.graph.downloadUrl": "https://contoso.sharepoint.com/dl?t=x",
            },
        )
        httpx_mock.add_response(
            url=re.compile(r"https://contoso\.sharepoint\.com/dl.*"),
            content=b"PK\x03\x04fake docx",
        )

        with patch("m365_brain.m365.extractors._attachment_helpers.convert_document", return_value="# Converted"):
            teams_chats.run(client, storage, {}, config, ctx)

        content = storage.read_file(_md(ctx.paths))
        assert "[report.docx](attachments/msg-conv/report.docx)" in content
        assert "[report.docx (text)](attachments_converted/msg-conv/report.docx.md)" in content


class TestNonDefaultLayout:
    def test_golden_chat_written_under_configured_layout(
        self, httpx_mock: HTTPXMock, storage, client, odd_ctx, teams_chat_fixture, teams_messages_fixture
    ):
        """Every key the extractor writes comes from config, not a literal.

        Asserted against a layout that shares no name with the conventional one:
        a golden fixture pinned to `teams-chats/messages.md` would still pass
        with the old hardcoded paths, which is the regression this catches.
        """
        httpx_mock.add_response(url=re.compile(r".*/me/chats\?.*"), json=teams_chat_fixture)
        httpx_mock.add_response(
            url=re.compile(r".*/me/chats/.*/messages.*"), json=teams_messages_fixture, is_reusable=True
        )

        _, count = teams_chats.run(client, storage, {}, _config(), odd_ctx)

        assert count == 2
        one_on_one = f"zz-inbox/chats/alice-johnson-bob-smith_{short_hash('19:meeting_abc123@thread.v2', 6)}"
        group = f"zz-inbox/chats/project-alpha_{short_hash('19:group_xyz789@thread.v2', 6)}"
        assert storage.list_files("zz-inbox") == sorted(
            [
                f"{one_on_one}/thread.md",
                f"{one_on_one}/thread.ndjson",
                f"{group}/thread.md",
                f"{group}/thread.ndjson",
            ]
        )
        # Nothing under the conventional layout — including inbox/teams-chats/.
        assert storage.list_files("inbox") == []
