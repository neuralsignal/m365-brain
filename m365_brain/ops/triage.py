"""Which received messages nobody has answered and nobody has declined to answer.

The rule is four clauses and every one of them is checkable: a message needs
attention when it arrived inside the window the caller asked for, sits in the
inbox folder, has no sibling in a sent folder sharing its conversation id, and
is not already recorded as rejected.

Two fields from the script this replaces are **gone**, and their absence is the
point:

* `has_question` was `"?" in body` reported under a field called "needs a
  reply". One character cannot support that conclusion, and an agent reading
  the message can see the question mark for itself.
* `body_word_count` was a measurement of a file the caller already has open.

What remains are the two facts the index can state and the reader cannot cheaply
recompute: whether the subject carries a forward prefix, and whether the
recipient was on the `to` line at all.

**Rejection is read from the outbox, not from a side file.** The original kept a
`rejected_threads.json` that only its own script wrote to. Here a rejection is
what reconciliation already records: a draft that was dispatched and whose
verdict came back `rejected` -- the human deleted it -- names the message it was
a reply to, and that message stops being triaged.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from m365_brain.config.ops import TriageConfig
from m365_brain.index.backends.base import IndexBackend
from m365_brain.index.query import parse_timeframe
from m365_brain.model import EntityRef, Observation
from m365_brain.ops.links import indexed_entities
from m365_brain.ops.names import email_addresses
from m365_brain.outbox.reconcile import Verdict
from m365_brain.outbox.stores import IntentStore
from m365_brain.vault.payloads import EmailForwardPayload, EmailReplyPayload

REJECTED_VERDICT: Verdict = "rejected"
"""The reconciliation verdict that means a human threw the draft away.

Typed against `outbox.reconcile.Verdict` so that renaming a verdict is a type
error here rather than a filter that silently stops matching.
"""


@dataclass(frozen=True, slots=True)
class MessageFields:
    """Which observation categories the message corpus writes each fact under.

    **A parameter because `ops.triage` has no field for it.** Every name here is
    a property of whoever produced the notes, so hardcoding one would ship one
    author's frontmatter vocabulary; the honest place for them is an
    `ops.triage` config block, and until that exists the caller states them.
    """

    entity_type: str
    folder: str
    conversation_id: str
    sender: str
    recipients: str
    timestamp: str


@dataclass(frozen=True, slots=True)
class TriageItem:
    """One message awaiting a reply."""

    entity: EntityRef
    subject: str
    sender: str | None
    received_at: datetime
    conversation_id: str
    is_forward: bool
    is_cc_only: bool


@dataclass(frozen=True, slots=True)
class _Message:
    """A message as the index states it, before any triage rule is applied."""

    entity: EntityRef
    folder: str
    conversation_id: str
    received_at: datetime
    sender: str | None
    recipients: frozenset[str]


def is_forward(subject: str, forward_prefixes: Sequence[str]) -> bool:
    """True when the subject opens with any configured forward prefix."""
    folded = subject.strip().casefold()
    return any(folded.startswith(prefix.casefold()) for prefix in forward_prefixes)


def is_cc_only(recipients: frozenset[str], own_email: str) -> bool:
    """True when the mailbox owner is not on the `to` line."""
    return own_email.casefold() not in recipients


def rejected_references(store: IntentStore) -> frozenset[str]:
    """Every message id a dispatched-then-rejected reply or forward pointed at.

    Walks the dispatched receipts because those are the only intents that can
    have a reconciliation verdict at all -- an intent rejected before dispatch
    never reached a human, so it says nothing about what the human wants.
    """
    references: set[str] = set()
    for receipt in store.dispatched_receipts():
        if store.reconciled_verdict(receipt.uuid) != REJECTED_VERDICT:
            continue
        envelope = store.archived_intent(receipt.uuid)
        if envelope is None:
            continue
        if isinstance(envelope.payload, EmailReplyPayload | EmailForwardPayload):
            references.add(envelope.payload.in_reply_to)
    return frozenset(references)


def triage(
    backend: IndexBackend,
    store: IntentStore,
    config: TriageConfig,
    fields: MessageFields,
    timeframe: str,
    now: datetime,
    page_size: int,
) -> list[TriageItem]:
    """Messages inside `timeframe` that are unanswered and not declined.

    Sent siblings are looked for across the **whole** index rather than inside
    the window: a reply written after the window closed still answers the
    message, and a window-limited search would resurface it forever.
    """
    moment = _as_utc(now)
    since = moment - parse_timeframe(timeframe)
    messages = [
        _message(backend, entity, fields) for entity in indexed_entities(backend, fields.entity_type, page_size)
    ]

    answered = {message.conversation_id for message in messages if message.folder in config.sent_folders}
    declined = rejected_references(store)

    return [
        TriageItem(
            entity=message.entity,
            subject=message.entity.title,
            sender=message.sender,
            received_at=message.received_at,
            conversation_id=message.conversation_id,
            is_forward=is_forward(message.entity.title, config.forward_prefixes),
            is_cc_only=is_cc_only(message.recipients, config.own_email),
        )
        for message in messages
        if message.folder == config.inbox_folder
        and message.received_at >= since
        and message.conversation_id not in answered
        and message.conversation_id not in declined
    ]


def _message(backend: IndexBackend, entity: EntityRef, fields: MessageFields) -> _Message:
    """One indexed entity read as a message. Missing structure raises."""
    observations = backend.get_observations(entity.entity_id)
    written = _required(entity, observations, fields.timestamp)
    try:
        received_at = _as_utc(datetime.fromisoformat(written))
    except ValueError as exc:
        raise ValueError(f"{entity.permalink}: [{fields.timestamp}] is not an ISO timestamp: {written!r}") from exc

    return _Message(
        entity=entity,
        folder=_required(entity, observations, fields.folder),
        conversation_id=_required(entity, observations, fields.conversation_id),
        received_at=received_at,
        sender=_first(observations, fields.sender),
        recipients=frozenset(
            address
            for observation in observations
            if observation.category == fields.recipients
            for address in email_addresses(observation.content)
        ),
    )


def _first(observations: Sequence[Observation], category: str) -> str | None:
    return next((o.content for o in observations if o.category == category), None)


def _required(entity: EntityRef, observations: Sequence[Observation], category: str) -> str:
    """An observation the triage rule cannot be evaluated without.

    Raises rather than skipping: a message with no folder or no conversation id
    cannot be judged either way, and dropping it would report "nothing needs
    attention" for a corpus that was simply mis-emitted.
    """
    content = _first(observations, category)
    if content is None:
        raise ValueError(f"{entity.permalink}: no observation in category {category!r}; triage cannot judge it")
    return content


def _as_utc(moment: datetime) -> datetime:
    """A naive timestamp is read as UTC -- see `tiers._as_utc` for the reasoning."""
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
