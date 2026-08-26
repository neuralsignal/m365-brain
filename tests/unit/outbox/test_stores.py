"""Store semantics, asserted identically against both implementations.

Every test here takes the parametrised `store` fixture, so each one runs twice.
The failure cases matter more than the happy ones: a fake that only implements
the happy path proves nothing, so a second claim must raise on both, and
`already_dispatched` must stay false until `archive` on both.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from m365_brain.outbox.stores import IntentAlreadyClaimed, IntentNotClaimed, IntentStore
from m365_brain.vault.dispatch import DispatchReceipt
from m365_brain.vault.intent import IntentParseError

from .conftest import DRAFT_PAYLOAD, intent_markdown


def _receipt(uuid: str, outcome: str = "dispatched") -> DispatchReceipt:
    return DispatchReceipt(
        uuid=uuid,
        kind="email.draft",
        outcome=outcome,
        dispatched_at=datetime(2026, 8, 5, 9, 0, tzinfo=UTC),
        graph_message_id="MSG-1" if outcome == "dispatched" else None,
        reason=None if outcome == "dispatched" else "graph_error",
        detail=None if outcome == "dispatched" else "boom",
    )


def test_both_implementations_satisfy_the_protocol(store):
    assert isinstance(store, IntentStore)


class TestPending:
    def test_a_placed_intent_is_pending_with_its_outbox_name(self, store, place):
        place("abc", "email.draft")

        assert list(store.pending()) == [("email.draft", "abc")]

    def test_a_claimed_intent_stops_being_pending(self, store, place):
        place("abc")
        store.claim("email.draft", "abc")

        assert list(store.pending()) == []

    def test_an_archived_intent_never_reappears_as_pending(self, store, place):
        place("abc")
        store.claim("email.draft", "abc")
        store.archive("abc", _receipt("abc"))

        assert list(store.pending()) == []


class TestClaim:
    def test_claiming_parses_the_intent(self, store, place):
        place("abc", payload=DRAFT_PAYLOAD, body="Hello there.")

        envelope = store.claim("email.draft", "abc")

        assert envelope.uuid == "abc"
        assert envelope.kind == "email.draft"
        assert envelope.payload.body.strip() == "Hello there."

    def test_a_second_claim_raises(self, store, place):
        place("abc")
        store.claim("email.draft", "abc")

        with pytest.raises(IntentAlreadyClaimed):
            store.claim("email.draft", "abc")

    def test_claiming_something_that_was_never_there_raises(self, store):
        with pytest.raises(IntentAlreadyClaimed):
            store.claim("email.draft", "never-existed")

    def test_a_claim_shows_up_in_flight(self, store, place):
        place("abc")
        store.claim("email.draft", "abc")

        assert store.inflight() == ["abc"]

    def test_an_unparseable_intent_is_still_claimed_so_it_can_be_archived(self, store):
        store.put("email.draft", "bad", "---\nuuid: bad\n---\nno payload here")

        with pytest.raises(IntentParseError):
            store.claim("email.draft", "bad")

        assert store.inflight() == ["bad"], "the rejection has to be archivable"


class TestArchive:
    def test_archiving_clears_the_in_flight_entry(self, store, place):
        place("abc")
        store.claim("email.draft", "abc")
        store.archive("abc", _receipt("abc"))

        assert store.inflight() == []

    def test_already_dispatched_flips_only_after_archive(self, store, place):
        place("abc")
        assert store.already_dispatched("abc") is False
        store.claim("email.draft", "abc")
        assert store.already_dispatched("abc") is False

        store.archive("abc", _receipt("abc"))

        assert store.already_dispatched("abc") is True

    def test_a_rejection_also_counts_as_dispatched_for_replay(self, store, place):
        """The archive is the ledger. A rejected intent must not be retried
        just because it did not succeed."""
        place("abc")
        store.claim("email.draft", "abc")
        store.archive("abc", _receipt("abc", outcome="failed"))

        assert store.already_dispatched("abc") is True

    def test_archiving_without_a_claim_raises(self, store):
        with pytest.raises(IntentNotClaimed):
            store.archive("abc", _receipt("abc"))

    def test_the_receipt_round_trips(self, store, place):
        place("abc")
        store.claim("email.draft", "abc")
        original = _receipt("abc")
        store.archive("abc", original)

        assert store.receipt("abc") == original

    def test_no_receipt_before_archive(self, store, place):
        place("abc")
        store.claim("email.draft", "abc")

        assert store.receipt("abc") is None

    def test_the_archived_intent_is_byte_identical(self, store, place):
        """It is the fixture reconciliation diffs against, so it must not be
        rewritten -- the implementation this replaces injected a rejection
        reason into the frontmatter and broke its own re-read."""
        content = place("abc", body="Original body text.")
        store.claim("email.draft", "abc")
        store.archive("abc", _receipt("abc"))

        archived = store.archived_intent("abc")
        assert archived is not None
        assert archived.payload.body.strip() == "Original body text."
        assert content  # the placed content is what was archived

    def test_no_archived_intent_for_an_unknown_uuid(self, store):
        assert store.archived_intent("nope") is None


class TestDispatchedReceipts:
    def test_only_dispatched_receipts_are_walked(self, store, place):
        for uuid, outcome in (("a", "dispatched"), ("b", "failed"), ("c", "dispatched")):
            place(uuid)
            store.claim("email.draft", uuid)
            store.archive(uuid, _receipt(uuid, outcome=outcome))

        assert [receipt.uuid for receipt in store.dispatched_receipts()] == ["a", "c"]


class TestReconciledMarker:
    def test_absent_until_marked(self, store, place):
        place("abc")
        store.claim("email.draft", "abc")
        store.archive("abc", _receipt("abc"))

        assert store.reconciled_verdict("abc") is None

        store.mark_reconciled("abc", "sent")

        assert store.reconciled_verdict("abc") == "sent"

    def test_a_marker_does_not_disturb_the_receipt(self, store, place):
        place("abc")
        store.claim("email.draft", "abc")
        store.archive("abc", _receipt("abc"))
        store.mark_reconciled("abc", "amended")

        assert store.receipt("abc") == _receipt("abc")
        assert [receipt.uuid for receipt in store.dispatched_receipts()] == ["abc"]


class TestIdentityChecks:
    def test_a_filename_stem_that_disagrees_with_the_envelope_is_a_parse_error(self, store):
        store.put("email.draft", "stem", intent_markdown("different", DRAFT_PAYLOAD, "body"))

        with pytest.raises(IntentParseError) as excinfo:
            store.claim("email.draft", "stem")

        assert "does not match the filename stem" in str(excinfo.value)


class TestRelease:
    """The inverse of `claim`, and the only way out of flight that is not terminal.

    Without it the store could take an intent and it could bury one, but it
    could not put one back -- so a dispatch that failed for a reason that will
    not still be true in five minutes had to be archived as permanently failed.
    """

    def test_a_released_intent_is_pending_again(self, store, place):
        place("abc")
        store.claim("email.draft", "abc")

        store.release("email.draft", "abc")

        assert list(store.pending()) == [("email.draft", "abc")]
        assert store.inflight() == []

    def test_a_released_intent_is_not_dispatched(self, store, place):
        """The ledger must not hold it: `already_dispatched` is what stops a
        retry, and the whole point of a release is that a retry is wanted."""
        place("abc")
        store.claim("email.draft", "abc")
        store.release("email.draft", "abc")

        assert store.already_dispatched("abc") is False
        assert store.receipt("abc") is None

    def test_the_released_content_is_byte_identical(self, store, place):
        content = place("abc", body="Original body text.")
        store.claim("email.draft", "abc")
        store.release("email.draft", "abc")

        envelope = store.claim("email.draft", "abc")

        assert envelope.payload.body.strip() == "Original body text."
        assert content

    def test_a_release_returns_it_to_its_own_outbox(self, store, place):
        """Not to the first configured one: a release that lost track of the
        outbox would re-dispatch under another outbox's authority."""
        place("xyz", "email.reply")
        store.claim("email.reply", "xyz")

        store.release("email.reply", "xyz")

        assert list(store.pending()) == [("email.reply", "xyz")]

    def test_releasing_something_not_in_flight_raises(self, store):
        with pytest.raises(IntentNotClaimed):
            store.release("email.draft", "abc")
