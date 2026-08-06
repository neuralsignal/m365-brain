"""The push and reconcile passes, against both stores and no transport at all.

There is no Graph here, and that is the design showing through: handlers arrive
as objects satisfying a Protocol and the reconciliation fetch arrives as a
callable, so the whole lifecycle -- replay detection, tier routing, per-intent
fail-safe, in-flight reporting -- is exercisable without a mocked HTTP layer.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from m365_brain.outbox.authority import AuthorityRouter
from m365_brain.outbox.reconcile import QuoteMarkers
from m365_brain.outbox.registry import build_registry
from m365_brain.outbox.runner import push, reconcile
from m365_brain.vault.dispatch import DRAFT_ONLY_OPS, DispatchResult, GraphOp
from m365_brain.vault.intent import IntentEnvelope

from .conftest import DRAFT_PAYLOAD, QUOTE_MARKERS, intent_markdown

ROUTER = AuthorityRouter()


@dataclass
class RecordingHandler:
    name: str
    declared_ops: frozenset[GraphOp] = field(default_factory=lambda: DRAFT_ONLY_OPS)
    calls: list[str] = field(default_factory=list)
    raises: Exception | None = None

    def execute(self, envelope: IntentEnvelope) -> DispatchResult:
        self.calls.append(envelope.uuid)
        if self.raises is not None:
            raise self.raises
        return DispatchResult(graph_message_id=f"MSG-{envelope.uuid}")


@pytest.fixture()
def handler():
    return RecordingHandler("email.draft")


@pytest.fixture()
def registry(outboxes_config, auth_profiles, handler):
    return build_registry(
        outboxes_config,
        auth_profiles,
        {
            "email.draft": handler,
            "teams.post_message": RecordingHandler("teams.post_message", frozenset({GraphOp.POST_CHANNEL})),
        },
    )


class TestDispatch:
    def test_a_draft_only_intent_executes_and_is_archived_with_its_message_id(self, store, place, registry, handler):
        place("abc")

        counts = push(store, registry, ROUTER)

        assert counts.dispatched == 1
        assert handler.calls == ["abc"]
        receipt = store.receipt("abc")
        assert receipt is not None
        assert receipt.outcome == "dispatched"
        assert receipt.graph_message_id == "MSG-abc"
        assert receipt.reason is None

    def test_a_dispatched_uuid_is_never_dispatched_twice(self, store, place, registry, handler):
        place("abc")
        push(store, registry, ROUTER)
        place("abc")

        counts = push(store, registry, ROUTER)

        assert counts.replayed == 1
        assert counts.dispatched == 0
        assert handler.calls == ["abc"], "the replay must not reach the handler"


class TestTierOutcomes:
    def test_a_never_auto_outbox_blocks_without_executing(self, store, place, outboxes_config, auth_profiles, handler):
        config = outboxes_config.model_copy(
            update={
                "definitions": {
                    **outboxes_config.definitions,
                    "email.draft": outboxes_config.definitions["email.draft"].model_copy(
                        update={"authority": "never_auto"}
                    ),
                }
            }
        )
        registry = build_registry(
            config,
            auth_profiles,
            {"email.draft": handler, "teams.post_message": RecordingHandler("teams.post_message")},
        )
        place("abc")

        counts = push(store, registry, ROUTER)

        assert counts.blocked == 1
        assert handler.calls == []
        assert store.receipt("abc").reason == "tier_blocked"

    def test_a_human_approval_intent_rejects_with_a_named_reason(
        self, store, place, outboxes_config, auth_profiles, handler
    ):
        """No approval surface exists in this build, so this is where such an
        intent ends. Visible and terminal beats a queue nothing drains."""
        config = outboxes_config.model_copy(
            update={
                "definitions": {
                    **outboxes_config.definitions,
                    "email.draft": outboxes_config.definitions["email.draft"].model_copy(
                        update={"authority": "human_approval"}
                    ),
                }
            }
        )
        registry = build_registry(
            config,
            auth_profiles,
            {"email.draft": handler, "teams.post_message": RecordingHandler("teams.post_message")},
        )
        place("abc")

        counts = push(store, registry, ROUTER)

        assert counts.failed == 1
        assert store.receipt("abc").reason == "no_approval_recorded"


class TestFailSafe:
    def test_one_failing_intent_does_not_stop_the_next(self, store, place, registry, handler):
        handler.raises = RuntimeError("graph exploded")
        place("aaa")
        place("bbb")

        counts = push(store, registry, ROUTER)

        assert counts.failed == 2
        assert handler.calls == ["aaa", "bbb"]

    def test_a_graph_failure_is_recorded_as_graph_error(self, store, place, registry, handler):
        handler.raises = RuntimeError("graph exploded")
        place("abc")

        push(store, registry, ROUTER)

        receipt = store.receipt("abc")
        assert receipt.reason == "graph_error"
        assert "graph exploded" in receipt.detail

    def test_a_missing_attachment_is_recorded_as_attachment_missing(self, store, place, registry, handler):
        handler.raises = FileNotFoundError("attachment not found: deck.pdf")
        place("abc")

        push(store, registry, ROUTER)

        assert store.receipt("abc").reason == "attachment_missing"

    def test_a_412_is_recorded_as_an_etag_conflict(self, store, place, registry, handler):
        """Recognised by `status_code`, not by exception class: the class lives
        in the half of the package this layer may not import."""

        class Conflict(Exception):
            status_code = 412

        handler.raises = Conflict("etag moved")
        place("abc")

        push(store, registry, ROUTER)

        assert store.receipt("abc").reason == "etag_conflict"

    def test_an_unparseable_intent_is_rejected_not_crashed_on(self, store, registry):
        store.put("email.draft", "bad", "---\nuuid: bad\n---\nno payload")

        counts = push(store, registry, ROUTER)

        assert counts.failed == 1
        assert store.receipt("bad").reason == "parse_error"
        assert store.already_dispatched("bad") is True, "a rejection must not be retried"


class TestInflight:
    def test_an_in_flight_intent_is_counted_and_never_retried(self, store, place, registry, handler):
        place("abc")
        store.claim("email.draft", "abc")  # a previous pass crashed here

        counts = push(store, registry, ROUTER)

        assert counts.inflight == 1
        assert counts.dispatched == 0
        assert handler.calls == [], "an unknown dispatch outcome must not be repeated"


class TestReconcile:
    @pytest.fixture()
    def markers(self):
        return QuoteMarkers.from_config(QUOTE_MARKERS)

    def _dispatch(self, store, registry, uuid: str, body: str) -> None:
        store.put("email.draft", uuid, intent_markdown(uuid, DRAFT_PAYLOAD, body))
        push(store, registry, ROUTER)

    def test_a_deleted_draft_is_a_rejection(self, store, registry, markers):
        self._dispatch(store, registry, "abc", "Hello there.")

        outcomes = reconcile(store, lambda mailbox, message_id, select: None, markers)

        assert [outcome.verdict for outcome in outcomes] == ["rejected"]
        assert outcomes[0].graph_message_id == "MSG-abc"

    def test_an_unedited_sent_draft_is_sent(self, store, registry, markers):
        self._dispatch(store, registry, "abc", "Hello there.")
        item = {"isDraft": False, "body": {"content": "<p>Hello there.</p>"}, "conversationId": "C1"}

        outcomes = reconcile(store, lambda *_: item, markers)

        assert outcomes[0].verdict == "sent"
        assert outcomes[0].conversation_id == "C1"
        assert outcomes[0].original_body.strip() == "Hello there."

    def test_an_edited_sent_draft_is_amended(self, store, registry, markers):
        self._dispatch(store, registry, "abc", "Hello there.")
        item = {"isDraft": False, "body": {"content": "<p>Completely different text.</p>"}}

        outcomes = reconcile(store, lambda *_: item, markers)

        assert outcomes[0].verdict == "amended"

    def test_a_settled_verdict_is_not_re_fetched(self, store, registry, markers):
        self._dispatch(store, registry, "abc", "Hello there.")
        calls: list[str] = []

        def fetch(mailbox, message_id, select):
            calls.append(message_id)
            return None

        reconcile(store, fetch, markers)
        reconcile(store, fetch, markers)

        assert calls == ["MSG-abc"], "a rejected draft is settled; re-asking Graph forever is the bug"

    def test_a_pending_draft_stays_open_for_the_next_pass(self, store, registry, markers):
        self._dispatch(store, registry, "abc", "Hello there.")
        item = {"isDraft": True, "body": {"content": "<p>Hello there.</p>"}}

        first = reconcile(store, lambda *_: item, markers)
        second = reconcile(store, lambda *_: item, markers)

        assert first[0].verdict == "pending"
        assert second[0].verdict == "pending"

    def test_the_fetch_is_asked_for_the_intents_own_mailbox(self, store, registry, markers):
        self._dispatch(store, registry, "abc", "Hello there.")
        seen: list[tuple[str, list[str]]] = []

        def fetch(mailbox, message_id, select):
            seen.append((mailbox, select))
            return None

        reconcile(store, fetch, markers)

        assert seen[0][0] == "me"
        assert "conversationId" in seen[0][1]
