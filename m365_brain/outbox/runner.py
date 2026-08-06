"""The two passes: push intents out, reconcile what came back.

Both are passes, not workers. A per-intent failure is recorded as an outcome
and the pass continues -- one bad intent never blocks the rest, which is the
fail-safe today's per-draft try/except provides, expressed as a receipt instead
of a swallowed exception.

Two rules the passes will not bend:

**An in-flight intent is never auto-retried.** A claim with no receipt means
the dispatch outcome is unknown, and retrying an unknown send duplicates mail.
`push` counts them and moves on; a human moves them back.

**A dispatched uuid is never dispatched twice.** The archive is the ledger and
`already_dispatched` is the replay check. Purging the processed archive re-arms
replay, which is a deliberate operator act rather than something guarded
against.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

import structlog

from m365_brain.outbox.authority import AuthorityRouter
from m365_brain.outbox.reconcile import (
    RECONCILE_SELECT,
    TERMINAL_VERDICTS,
    QuoteMarkers,
    ReconcileOutcome,
    classify,
)
from m365_brain.outbox.registry import OutboxRegistry, UnknownOutbox
from m365_brain.outbox.stores import IntentAlreadyClaimed, IntentStore
from m365_brain.vault.dispatch import DispatchReceipt, NonDispatchReason
from m365_brain.vault.intent import IntentParseError

log = structlog.get_logger()

ETAG_CONFLICT_STATUS = 412

FetchMessage = Callable[[str, str, list[str]], dict | None]
"""`(mailbox, message_id, select) -> message | None`, injected by the caller.

The fetch is not imported here. `outbox` and `m365` are peers in the layer map,
so the Graph call arrives as a callable -- which also means the whole
reconciliation pass is testable with no transport in sight.
"""


@dataclass
class PushCounts:
    """What one push pass did. Every intent lands in exactly one bucket."""

    dispatched: int = 0
    blocked: int = 0
    failed: int = 0
    replayed: int = 0
    contended: int = 0
    inflight: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "dispatched": self.dispatched,
            "blocked": self.blocked,
            "failed": self.failed,
            "replayed": self.replayed,
            "contended": self.contended,
            "inflight": self.inflight,
        }


@dataclass
class _Failure:
    reason: NonDispatchReason
    detail: str
    blocked: bool = field(default=False)


def push(store: IntentStore, registry: OutboxRegistry, router: AuthorityRouter) -> PushCounts:
    """Claim, route, dispatch, receipt, archive -- once per pending intent."""
    counts = PushCounts()
    for outbox_name, uuid in list(store.pending()):
        if store.already_dispatched(uuid):
            counts.replayed += 1
            log.info("outbox.replay_skipped", uuid=uuid, outbox=outbox_name)
            continue
        try:
            envelope = store.claim(outbox_name, uuid)
        except IntentAlreadyClaimed:
            counts.contended += 1
            continue
        except IntentParseError as exc:
            _record(store, uuid, "", None, _Failure("parse_error", str(exc)), counts)
            continue
        _dispatch_one(store, registry, router, outbox_name, uuid, envelope, counts)
    counts.inflight = len(store.inflight())
    if counts.inflight:
        log.warning("outbox.inflight_intents", uuids=store.inflight())
    return counts


def _dispatch_one(store, registry, router, outbox_name, uuid, envelope, counts) -> None:
    try:
        outbox = registry.get(outbox_name)
    except UnknownOutbox as exc:
        _record(store, uuid, envelope.kind, None, _Failure("unknown_outbox", str(exc)), counts)
        return

    action = router.next_action(outbox.authority, "pending")
    if action == "await_admin":
        _record(
            store,
            uuid,
            envelope.kind,
            None,
            _Failure("tier_blocked", f"outbox {outbox_name!r} is authority {outbox.authority.value}", blocked=True),
            counts,
        )
        return
    if action == "await_approval":
        # No approval surface exists, so this is where a human_approval intent
        # ends. Visible and terminal beats a queue nothing drains.
        _record(
            store,
            uuid,
            envelope.kind,
            None,
            _Failure("no_approval_recorded", f"outbox {outbox_name!r} requires an approval this build cannot record"),
            counts,
        )
        return

    try:
        result = outbox.handler.execute(envelope)
    except Exception as exc:  # noqa: BLE001 -- one bad intent must not stop the pass
        log.error("outbox.dispatch_failed", uuid=uuid, outbox=outbox_name, error=str(exc), exc_info=True)
        _record(store, uuid, envelope.kind, None, _classify_failure(exc), counts)
        return

    store.archive(
        uuid,
        DispatchReceipt(
            uuid=uuid,
            kind=envelope.kind,
            outcome="dispatched",
            dispatched_at=datetime.now(UTC),
            graph_message_id=result.graph_message_id,
            reason=None,
            detail=None,
        ),
    )
    counts.dispatched += 1
    log.info("outbox.dispatched", uuid=uuid, outbox=outbox_name, graph_message_id=result.graph_message_id)


def _classify_failure(exc: Exception) -> _Failure:
    """Map a handler failure onto a receipt reason.

    Duck-typed on `status_code` rather than on `GraphConflictError` by name:
    that class lives in the Microsoft 365 half of the package, which this layer
    may not import. The attribute is part of the transport's public surface, so
    reading it is not a trick -- but it is worth saying why it is read.
    """
    if isinstance(exc, FileNotFoundError):
        return _Failure("attachment_missing", str(exc))
    if getattr(exc, "status_code", None) == ETAG_CONFLICT_STATUS:
        return _Failure("etag_conflict", str(exc))
    return _Failure("graph_error", str(exc))


def _record(store, uuid: str, kind: str, message_id: str | None, failure: _Failure, counts: PushCounts) -> None:
    """Archive a non-dispatch as an outcome. Never raises out of the pass."""
    store.archive(
        uuid,
        DispatchReceipt(
            uuid=uuid,
            kind=kind,
            outcome="blocked" if failure.blocked else "failed",
            dispatched_at=datetime.now(UTC),
            graph_message_id=message_id,
            reason=failure.reason,
            detail=failure.detail,
        ),
    )
    if failure.blocked:
        counts.blocked += 1
    else:
        counts.failed += 1
    log.info("outbox.not_dispatched", uuid=uuid, reason=failure.reason, detail=failure.detail)


def reconcile(store: IntentStore, fetch: FetchMessage, markers: QuoteMarkers) -> list[ReconcileOutcome]:
    """Ask Graph what became of every open dispatched intent.

    Returns outcomes for the caller's `post_reconcile` hooks to file. Nothing
    here touches a knowledge base: the outcome carries the sent HTML and the
    original body by value, so no path crosses the boundary in either
    direction.
    """
    outcomes: list[ReconcileOutcome] = []
    for receipt in store.dispatched_receipts():
        if receipt.graph_message_id is None:
            continue
        if store.reconciled_verdict(receipt.uuid) in TERMINAL_VERDICTS:
            continue
        envelope = store.archived_intent(receipt.uuid)
        if envelope is None or not hasattr(envelope.payload, "mailbox"):
            # Only mailbox-bound intents have a fate to discover. A channel
            # post or a file write is done the moment it is dispatched.
            continue
        item = fetch(envelope.payload.mailbox, receipt.graph_message_id, RECONCILE_SELECT)
        outcome = classify(receipt, item, envelope.payload.body, markers)
        store.mark_reconciled(receipt.uuid, outcome.verdict)
        outcomes.append(outcome)
        log.info("outbox.reconciled", uuid=receipt.uuid, verdict=outcome.verdict)
    return outcomes
