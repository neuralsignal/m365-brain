"""The vocabulary the lifecycle and the executors share.

Mostly declarations, so the tests are mostly about what must *not* drift: the
draft-only operation set, the closed rejection-reason vocabulary, and the fact
that a handler satisfies `OutboxHandler` without importing it.
"""

from __future__ import annotations

import typing
from dataclasses import dataclass

import pytest
from pydantic import ValidationError

from m365_brain.vault.dispatch import (
    DRAFT_ONLY_OPS,
    DispatchReceipt,
    DispatchResult,
    GraphOp,
    OutboxHandler,
    RejectionReason,
)
from m365_brain.vault.intent import IntentEnvelope


def test_draft_only_ops_excludes_everything_that_leaves_the_mailbox():
    assert frozenset({GraphOp.CREATE_DRAFT, GraphOp.UPDATE_DRAFT, GraphOp.ATTACH}) == DRAFT_ONLY_OPS
    assert GraphOp.SEND_MAIL not in DRAFT_ONLY_OPS
    assert GraphOp.POST_CHANNEL not in DRAFT_ONLY_OPS
    assert GraphOp.PUT_FILE not in DRAFT_ONLY_OPS


def test_the_rejection_reasons_are_a_closed_set():
    """An operator greps receipts by reason. Free text would make the archive
    unqueryable exactly when someone needs to ask why 40 intents failed."""
    assert set(typing.get_args(RejectionReason)) == {
        "tier_blocked",
        "no_approval_recorded",
        "etag_conflict",
        "graph_error",
        "attachment_missing",
        "parse_error",
        "unknown_outbox",
    }


class TestDispatchResult:
    def test_it_carries_what_the_dispatch_produced(self):
        assert DispatchResult(graph_message_id="MSG-1").graph_message_id == "MSG-1"

    def test_a_dispatch_with_nothing_to_point_at_is_still_expressible(self):
        assert DispatchResult(graph_message_id=None).graph_message_id is None

    def test_it_is_frozen(self):
        result = DispatchResult(graph_message_id="MSG-1")
        with pytest.raises(ValidationError):
            result.graph_message_id = "MSG-2"


class TestReceipt:
    def _receipt(self, **overrides) -> DispatchReceipt:
        return DispatchReceipt(
            **{
                "uuid": "abc",
                "kind": "email.draft",
                "outcome": "dispatched",
                "dispatched_at": "2026-08-05T09:00:00Z",
                "graph_message_id": "MSG-1",
                "reason": None,
                "detail": None,
                **overrides,
            }
        )

    def test_it_round_trips_through_json(self):
        original = self._receipt()

        assert DispatchReceipt.model_validate_json(original.model_dump_json()) == original

    def test_an_unknown_reason_is_rejected(self):
        with pytest.raises(ValidationError):
            self._receipt(outcome="rejected", reason="because")

    def test_an_unknown_outcome_is_rejected(self):
        with pytest.raises(ValidationError):
            self._receipt(outcome="maybe")

    def test_an_unknown_field_is_rejected(self):
        with pytest.raises(ValidationError):
            self._receipt(retries=3)


def test_a_handler_satisfies_the_protocol_structurally():
    """A Protocol is what lets the executors live in the peer subpackage: they
    satisfy this without importing it, so no import crosses the layer."""

    @dataclass
    class Handler:
        name: str = "email.draft"
        declared_ops: frozenset[GraphOp] = DRAFT_ONLY_OPS

        def execute(self, envelope: IntentEnvelope) -> DispatchResult:
            return DispatchResult(graph_message_id=None)

    assert isinstance(Handler(), OutboxHandler)


def test_something_missing_execute_does_not_satisfy_it():
    @dataclass
    class NotAHandler:
        name: str = "x"
        declared_ops: frozenset[GraphOp] = DRAFT_ONLY_OPS

    assert not isinstance(NotAHandler(), OutboxHandler)
