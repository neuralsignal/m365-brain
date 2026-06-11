"""Tests for the per-conversation JSONL message store."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from m365_extract.extractors._message_store import (
    StoredMessage,
    load_store,
    merge_messages,
    save_store,
)
from m365_extract.extractors.errors import MessageStoreError


class MemoryStorage:
    """Minimal in-memory StorageBackend for store round-trip tests."""

    def __init__(self) -> None:
        self.files: dict[str, str] = {}

    def write_file(self, path: str, content: str) -> None:
        self.files[path] = content

    def read_file(self, path: str) -> str:
        return self.files[path]

    def file_exists(self, path: str) -> bool:
        return path in self.files

    def list_files(self, prefix: str) -> list[str]:
        return sorted(p for p in self.files if p.startswith(prefix))

    def delete_file(self, path: str) -> None:
        del self.files[path]

    def write_bytes(self, path: str, content: bytes) -> None:
        raise NotImplementedError


_iso_timestamps = st.datetimes(
    min_value=datetime(2020, 1, 1),
    max_value=datetime(2030, 12, 31),
).map(lambda d: d.strftime("%Y-%m-%dT%H:%M:%S") + "Z")

_attachment_dicts = st.fixed_dictionaries(
    {
        "name": st.text(max_size=20),
        "relative_path": st.text(max_size=40),
        "converted_path": st.none() | st.text(max_size=40),
    }
)

_messages = st.builds(
    StoredMessage,
    id=st.uuids().map(str),
    parent_id=st.none() | st.uuids().map(str),
    sender=st.text(max_size=30),
    created=_iso_timestamps,
    last_modified=_iso_timestamps,
    etag=st.text(min_size=1, max_size=10),
    edited=st.booleans(),
    deleted=st.booleans(),
    content=st.text(max_size=200),
    attachments=st.lists(_attachment_dicts, max_size=2),
    subject=st.none() | st.text(max_size=40),
)


def _store_from(messages: list[StoredMessage]) -> dict[str, StoredMessage]:
    return {m.id: m for m in messages}


def _message(msg_id: str, created: str, etag: str, content: str) -> StoredMessage:
    return StoredMessage(
        id=msg_id,
        parent_id=None,
        sender="Alice",
        created=created,
        last_modified=created,
        etag=etag,
        edited=False,
        deleted=False,
        content=content,
        attachments=[],
        subject=None,
    )


class TestRoundTrip:
    @given(messages=st.lists(_messages, max_size=10, unique_by=lambda m: m.id))
    @settings(max_examples=50)
    def test_save_then_load_returns_same_store(self, messages: list[StoredMessage]) -> None:
        storage = MemoryStorage()
        store = _store_from(messages)
        save_store(storage, "conv/messages.jsonl", store)
        assert load_store(storage, "conv/messages.jsonl") == store

    def test_load_missing_file_returns_empty(self) -> None:
        assert load_store(MemoryStorage(), "conv/messages.jsonl") == {}

    def test_save_writes_one_json_object_per_line_sorted(self) -> None:
        storage = MemoryStorage()
        m1 = _message("b-id", "2026-06-11T09:00:00Z", "1", "later")
        m2 = _message("a-id", "2026-06-10T09:00:00Z", "1", "earlier")
        save_store(storage, "conv/messages.jsonl", _store_from([m1, m2]))
        lines = [ln for ln in storage.files["conv/messages.jsonl"].splitlines() if ln]
        assert [json.loads(ln)["id"] for ln in lines] == ["a-id", "b-id"]

    def test_save_sorts_by_id_within_same_created(self) -> None:
        storage = MemoryStorage()
        same = "2026-06-11T09:00:00Z"
        save_store(
            storage,
            "conv/messages.jsonl",
            _store_from([_message("z", same, "1", "z"), _message("a", same, "1", "a")]),
        )
        lines = [ln for ln in storage.files["conv/messages.jsonl"].splitlines() if ln]
        assert [json.loads(ln)["id"] for ln in lines] == ["a", "z"]


class TestCorruptStore:
    def test_corrupt_json_line_raises(self) -> None:
        storage = MemoryStorage()
        storage.files["conv/messages.jsonl"] = '{"id": "ok"\nnot-json\n'
        with pytest.raises(MessageStoreError, match="conv/messages.jsonl"):
            load_store(storage, "conv/messages.jsonl")

    def test_valid_json_missing_fields_raises(self) -> None:
        storage = MemoryStorage()
        storage.files["conv/messages.jsonl"] = json.dumps({"id": "m1"}) + "\n"
        with pytest.raises(MessageStoreError, match="line 1"):
            load_store(storage, "conv/messages.jsonl")

    def test_non_object_line_raises(self) -> None:
        storage = MemoryStorage()
        storage.files["conv/messages.jsonl"] = '["not", "an", "object"]\n'
        with pytest.raises(MessageStoreError):
            load_store(storage, "conv/messages.jsonl")


class TestMerge:
    @given(
        existing=st.lists(_messages, max_size=8, unique_by=lambda m: m.id),
        fetched=st.lists(_messages, max_size=8, unique_by=lambda m: m.id),
    )
    @settings(max_examples=50)
    def test_merge_is_idempotent(self, existing: list[StoredMessage], fetched: list[StoredMessage]) -> None:
        store = _store_from(existing)
        once, _ = merge_messages(store, fetched)
        twice, changed_again = merge_messages(once, fetched)
        assert twice == once
        assert changed_again is False

    @given(
        existing=st.lists(_messages, max_size=8, unique_by=lambda m: m.id),
        fetched=st.lists(_messages, max_size=8, unique_by=lambda m: m.id),
    )
    @settings(max_examples=50)
    def test_merge_never_drops_ids(self, existing: list[StoredMessage], fetched: list[StoredMessage]) -> None:
        store = _store_from(existing)
        merged, _ = merge_messages(store, fetched)
        assert set(store) <= set(merged)
        assert {m.id for m in fetched} <= set(merged)

    def test_new_message_inserted_and_changed(self) -> None:
        msg = _message("m1", "2026-06-11T09:00:00Z", "1", "hello")
        merged, changed = merge_messages({}, [msg])
        assert merged == {"m1": msg}
        assert changed is True

    def test_same_etag_keeps_existing_and_unchanged(self) -> None:
        old = _message("m1", "2026-06-11T09:00:00Z", "1", "original")
        refetched = replace(old, content="should not replace")
        merged, changed = merge_messages({"m1": old}, [refetched])
        assert merged["m1"].content == "original"
        assert changed is False

    def test_etag_change_replaces_content(self) -> None:
        old = _message("m1", "2026-06-11T09:00:00Z", "1", "original")
        edited = replace(old, etag="2", content="edited", edited=True)
        merged, changed = merge_messages({"m1": old}, [edited])
        assert merged["m1"].content == "edited"
        assert merged["m1"].edited is True
        assert changed is True

    def test_merge_does_not_mutate_input_store(self) -> None:
        old = _message("m1", "2026-06-11T09:00:00Z", "1", "original")
        store = {"m1": old}
        merge_messages(store, [replace(old, etag="2", content="new")])
        assert store["m1"].content == "original"
