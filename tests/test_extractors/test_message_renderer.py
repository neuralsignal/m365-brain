"""Tests for the standardized message timeline renderer."""

from __future__ import annotations

from datetime import datetime

from hypothesis import given, settings
from hypothesis import strategies as st

from m365_extract.extractors._message_renderer import render_channel_body, render_chat_body
from m365_extract.extractors._message_store import StoredMessage


def _msg(
    msg_id: str,
    created: str,
    *,
    parent_id: str | None = None,
    sender: str = "Alice",
    content: str = "hello",
    edited: bool = False,
    deleted: bool = False,
    attachments: list[dict] | None = None,
    subject: str | None = None,
) -> StoredMessage:
    return StoredMessage(
        id=msg_id,
        parent_id=parent_id,
        sender=sender,
        created=created,
        last_modified=created,
        etag="1",
        edited=edited,
        deleted=deleted,
        content=content,
        attachments=attachments if attachments is not None else [],
        subject=subject,
    )


def _store(*messages: StoredMessage) -> dict[str, StoredMessage]:
    return {m.id: m for m in messages}


_iso_timestamps = st.datetimes(
    min_value=datetime(2020, 1, 1),
    max_value=datetime(2030, 12, 31),
).map(lambda d: d.strftime("%Y-%m-%dT%H:%M:%S") + "Z")

_messages = st.builds(
    StoredMessage,
    id=st.uuids().map(str),
    parent_id=st.none(),
    sender=st.text(min_size=1, max_size=20),
    created=_iso_timestamps,
    last_modified=_iso_timestamps,
    etag=st.just("1"),
    edited=st.booleans(),
    deleted=st.booleans(),
    content=st.text(max_size=100),
    attachments=st.just([]),
    subject=st.none(),
)


class TestDeterminism:
    @given(messages=st.lists(_messages, max_size=8, unique_by=lambda m: m.id))
    @settings(max_examples=50)
    def test_chat_render_is_deterministic_and_order_independent(self, messages: list[StoredMessage]) -> None:
        store = _store(*messages)
        reordered = dict(reversed(list(store.items())))
        assert render_chat_body(store) == render_chat_body(reordered)
        assert render_chat_body(store) == render_chat_body(store)

    @given(messages=st.lists(_messages, max_size=8, unique_by=lambda m: m.id))
    @settings(max_examples=50)
    def test_channel_render_is_deterministic_and_order_independent(self, messages: list[StoredMessage]) -> None:
        store = _store(*messages)
        reordered = dict(reversed(list(store.items())))
        assert render_channel_body(store) == render_channel_body(reordered)


class TestChatRendering:
    def test_day_grouping_and_headers(self) -> None:
        body = render_chat_body(
            _store(
                _msg("m1", "2026-06-11T09:42:00Z", sender="Matthias", content="first"),
                _msg("m2", "2026-06-11T10:20:00Z", sender="Samuel", content="second"),
                _msg("m3", "2026-06-12T08:00:00Z", sender="Matthias", content="next day"),
            )
        )
        assert body.index("## 2026-06-11") < body.index("### 09:42 — Matthias")
        assert body.index("### 09:42 — Matthias") < body.index("### 10:20 — Samuel")
        assert body.index("### 10:20 — Samuel") < body.index("## 2026-06-12")
        assert body.count("## 2026-06-11") == 1
        assert "first" in body and "second" in body and "next day" in body

    def test_messages_sorted_by_created_then_id(self) -> None:
        body = render_chat_body(
            _store(
                _msg("b", "2026-06-11T09:00:00Z", content="from-b"),
                _msg("a", "2026-06-11T09:00:00Z", content="from-a"),
            )
        )
        assert body.index("from-a") < body.index("from-b")

    def test_edited_marker(self) -> None:
        body = render_chat_body(_store(_msg("m1", "2026-06-11T10:20:00Z", sender="Samuel", edited=True)))
        assert "### 10:20 — Samuel *(edited)*" in body

    def test_deleted_marker_and_tombstone_body(self) -> None:
        body = render_chat_body(_store(_msg("m1", "2026-06-11T10:20:00Z", sender="Samuel", deleted=True, content="")))
        assert "### 10:20 — Samuel *(deleted)*" in body
        assert "*Message deleted.*" in body

    def test_attachment_links_line(self) -> None:
        body = render_chat_body(
            _store(
                _msg(
                    "m1",
                    "2026-06-11T09:42:00Z",
                    attachments=[
                        {
                            "name": "report.pdf",
                            "relative_path": "attachments/m1/report.pdf",
                            "converted_path": "attachments_converted/m1/report.pdf.md",
                        }
                    ],
                )
            )
        )
        assert (
            "**Attachments:** [report.pdf](attachments/m1/report.pdf)"
            " · [report.pdf (text)](attachments_converted/m1/report.pdf.md)" in body
        )

    def test_attachment_without_converted_path_has_no_text_link(self) -> None:
        body = render_chat_body(
            _store(
                _msg(
                    "m1",
                    "2026-06-11T09:42:00Z",
                    attachments=[{"name": "x.zip", "relative_path": "attachments/m1/x.zip", "converted_path": None}],
                )
            )
        )
        assert "**Attachments:** [x.zip](attachments/m1/x.zip)" in body
        assert "(text)" not in body

    def test_empty_store_renders_empty_string(self) -> None:
        assert render_chat_body({}) == ""


class TestChannelRendering:
    def test_thread_title_from_subject(self) -> None:
        body = render_channel_body(_store(_msg("root", "2026-06-11T09:42:00Z", sender="Alice", subject="Release plan")))
        assert "### 09:42 — Alice — Release plan" in body

    def test_thread_title_from_first_content_line_truncated(self) -> None:
        long_line = "x" * 80
        body = render_channel_body(
            _store(_msg("root", "2026-06-11T09:42:00Z", sender="Alice", content=f"\n{long_line}\nmore"))
        )
        assert f"### 09:42 — Alice — {'x' * 60}" in body.split("\n")

    def test_thread_title_fallback_to_thread(self) -> None:
        body = render_channel_body(_store(_msg("root", "2026-06-11T09:42:00Z", sender="Alice", content="")))
        assert "### 09:42 — Alice — Thread" in body

    def test_replies_render_under_root_with_arrow(self) -> None:
        body = render_channel_body(
            _store(
                _msg("root", "2026-06-11T09:42:00Z", sender="Alice", subject="Topic", content="root msg"),
                _msg("r1", "2026-06-11T10:01:00Z", parent_id="root", sender="Bob", content="reply msg"),
            )
        )
        assert "#### ↳ 10:01 — Bob" in body
        assert body.index("root msg") < body.index("#### ↳ 10:01 — Bob")

    def test_cross_day_reply_includes_date_in_header(self) -> None:
        body = render_channel_body(
            _store(
                _msg("root", "2026-06-11T09:42:00Z", sender="Alice", subject="Topic"),
                _msg("r1", "2026-06-12T08:15:00Z", parent_id="root", sender="Bob"),
            )
        )
        assert "#### ↳ 2026-06-12 08:15 — Bob" in body
        # The whole thread renders under the root's day heading.
        assert "## 2026-06-12" not in body

    def test_replies_sorted_by_created_then_id(self) -> None:
        body = render_channel_body(
            _store(
                _msg("root", "2026-06-11T09:00:00Z", sender="Alice", subject="T"),
                _msg("r2", "2026-06-11T11:00:00Z", parent_id="root", content="later reply"),
                _msg("r1", "2026-06-11T10:00:00Z", parent_id="root", content="earlier reply"),
            )
        )
        assert body.index("earlier reply") < body.index("later reply")

    def test_orphaned_reply_rendered_top_level_with_marker(self) -> None:
        body = render_channel_body(
            _store(_msg("r1", "2026-06-11T10:01:00Z", parent_id="gone", sender="Bob", content="orphan content"))
        )
        assert "### 10:01 — Bob *(orphaned reply)*" in body
        assert "orphan content" in body
        assert "↳" not in body

    def test_threads_grouped_by_root_day(self) -> None:
        body = render_channel_body(
            _store(
                _msg("root1", "2026-06-11T09:00:00Z", sender="Alice", subject="Day one"),
                _msg("root2", "2026-06-12T09:00:00Z", sender="Alice", subject="Day two"),
            )
        )
        assert body.index("## 2026-06-11") < body.index("Day one")
        assert body.index("Day one") < body.index("## 2026-06-12")
        assert body.index("## 2026-06-12") < body.index("Day two")

    def test_deleted_root_renders_tombstone(self) -> None:
        body = render_channel_body(
            _store(_msg("root", "2026-06-11T09:42:00Z", sender="Alice", deleted=True, content=""))
        )
        assert "*(deleted)*" in body
        assert "*Message deleted.*" in body
