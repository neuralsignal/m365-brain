"""What only the filesystem store can be asked: where the bytes actually land.

`test_stores.py` covers the semantics both implementations share. These are the
properties that exist because this one writes files -- every path from config,
nothing from a literal, and the archives out of the listing path.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from m365_brain.outbox.filesystem_store import FilesystemIntentStore
from m365_brain.storage.local import LocalBackend
from m365_brain.vault.dispatch import DispatchReceipt

from .conftest import DRAFT_PAYLOAD, OUTBOX_NAMES, intent_markdown


@pytest.fixture()
def backend(tmp_path):
    return LocalBackend(str(tmp_path / "vault"))


@pytest.fixture()
def fs_store(backend, paths):
    return FilesystemIntentStore(backend, paths, OUTBOX_NAMES)


def _receipt(uuid: str, outcome: str) -> DispatchReceipt:
    return DispatchReceipt(
        uuid=uuid,
        kind="email.draft",
        outcome=outcome,
        dispatched_at=datetime(2026, 8, 5, 9, 0, tzinfo=UTC),
        graph_message_id="MSG-1",
        reason=None if outcome == "dispatched" else "tier_blocked",
        detail=None if outcome == "dispatched" else "blocked",
    )


def _place(fs_store, uuid: str) -> None:
    fs_store.put("email.draft", uuid, intent_markdown(uuid, DRAFT_PAYLOAD, "body"))


class TestLayout:
    def test_every_path_comes_from_the_configured_layout(self, fs_store, backend):
        _place(fs_store, "abc")

        assert backend.file_exists("pending/email.draft/abc.md")

    def test_a_claim_lands_in_the_configured_inflight_directory(self, fs_store, backend):
        _place(fs_store, "abc")
        fs_store.claim("email.draft", "abc")

        assert backend.file_exists("dot-meta/claimed/abc.md")
        assert not backend.file_exists("pending/email.draft/abc.md")

    def test_a_dispatch_archives_intent_and_receipt_side_by_side(self, fs_store, backend):
        _place(fs_store, "abc")
        fs_store.claim("email.draft", "abc")
        fs_store.archive("abc", _receipt("abc", "dispatched"))

        assert backend.file_exists("dot-meta/done/abc.md")
        assert backend.file_exists("dot-meta/done/abc.receipt.json")
        assert not backend.file_exists("dot-meta/claimed/abc.md")

    def test_a_rejection_archives_into_the_rejected_tree_with_its_own_receipt(self, fs_store, backend):
        _place(fs_store, "abc")
        fs_store.claim("email.draft", "abc")
        fs_store.archive("abc", _receipt("abc", "blocked"))

        assert backend.file_exists("dot-meta/refused/abc.md")
        assert backend.file_exists("dot-meta/refused/abc.receipt.json")
        assert not backend.file_exists("dot-meta/done/abc.md")

    def test_the_archived_bytes_equal_the_submitted_bytes(self, fs_store, backend):
        submitted = intent_markdown("abc", DRAFT_PAYLOAD, "body")
        fs_store.put("email.draft", "abc", submitted)
        fs_store.claim("email.draft", "abc")
        fs_store.archive("abc", _receipt("abc", "dispatched"))

        assert backend.read_file("dot-meta/done/abc.md") == submitted


class TestListing:
    def test_pending_lists_only_configured_outboxes(self, fs_store, backend):
        _place(fs_store, "abc")
        backend.write_file("pending/not.configured/zzz.md", "ignored")

        assert list(fs_store.pending()) == [("email.draft", "abc")]

    def test_pending_never_descends_into_the_archives(self, fs_store, backend):
        """The implementation this replaces listed the whole outbox subtree on
        every tick, archives included, and filtered afterwards."""
        _place(fs_store, "abc")
        backend.write_file("dot-meta/done/old.md", "archived")
        backend.write_file("dot-meta/refused/older.md", "archived")

        assert list(fs_store.pending()) == [("email.draft", "abc")]

    def test_a_non_markdown_file_in_an_outbox_is_reported_and_skipped(self, fs_store, backend):
        _place(fs_store, "abc")
        backend.write_file("pending/email.draft/notes.txt", "stray")

        assert list(fs_store.pending()) == [("email.draft", "abc")]

    def test_putting_into_an_unconfigured_outbox_raises(self, fs_store):
        with pytest.raises(KeyError):
            fs_store.put("file.update", "abc", "content")

    def test_the_reconciled_marker_is_not_mistaken_for_a_receipt(self, fs_store):
        _place(fs_store, "abc")
        fs_store.claim("email.draft", "abc")
        fs_store.archive("abc", _receipt("abc", "dispatched"))
        fs_store.mark_reconciled("abc", "sent")

        assert [receipt.uuid for receipt in fs_store.dispatched_receipts()] == ["abc"]


class TestConcurrency:
    def test_a_second_runner_cannot_claim_what_is_already_in_flight(self, backend, paths):
        first = FilesystemIntentStore(backend, paths, OUTBOX_NAMES)
        second = FilesystemIntentStore(backend, paths, OUTBOX_NAMES)
        _place(first, "abc")
        first.claim("email.draft", "abc")

        from m365_brain.outbox.stores import IntentAlreadyClaimed

        with pytest.raises(IntentAlreadyClaimed):
            second.claim("email.draft", "abc")

    def test_a_crash_between_claim_and_receipt_leaves_it_in_flight(self, fs_store):
        _place(fs_store, "abc")
        fs_store.claim("email.draft", "abc")

        assert fs_store.inflight() == ["abc"]
        assert list(fs_store.pending()) == [], "it must not be re-dispatched by the next pass"
