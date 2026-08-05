"""Where intents live between being written and being archived.

The protocol is deliberately small and deliberately has two implementations
from the first commit. A one-implementation Protocol is a shape nobody has
tested against, and the fake is what keeps this one honest: the whole outbox
suite runs against both, and a test that passes on one and not the other has
found a real difference rather than a mocking artefact.

`pending()` yields identifiers, not parsed envelopes. Parsing belongs after
the claim, because an unparseable intent still has to be archived with a
receipt, and archiving needs the claim to have happened first. A `pending()`
that parsed would either raise past the runner or swallow, and both lose the
rejection.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Protocol, runtime_checkable

from m365_brain.vault.dispatch import DispatchReceipt
from m365_brain.vault.intent import IntentEnvelope, parse_intent


class IntentAlreadyClaimed(Exception):
    """The intent is gone: another runner claimed it, or it was never there."""


class IntentNotClaimed(Exception):
    """Archive was called for a uuid with nothing in flight. A caller bug."""


@runtime_checkable
class IntentStore(Protocol):
    """The seam between the outbox lifecycle and wherever intents are kept."""

    def put(self, outbox_name: str, uuid: str, content: str) -> None:
        """Place an intent in an outbox. The authoring entry point.

        Takes raw markdown rather than an envelope: an author writes a file,
        and round-tripping it through the model before it is even claimed would
        make the store the thing that decides whether an intent is valid --
        which is the claim's job, so the rejection can be archived.
        """
        ...

    def pending(self) -> Iterator[tuple[str, str]]:
        """`(outbox_name, uuid)` for every unclaimed intent. Never descends
        into the archives -- the ported implementation listed the whole subtree
        on every tick and filtered afterwards, so its cost grew with the
        archive forever."""
        ...

    def claim(self, outbox_name: str, uuid: str) -> IntentEnvelope:
        """Move the intent in flight and parse it.

        Raises `IntentAlreadyClaimed` when it is gone, and `IntentParseError`
        when it is unparseable -- the latter *after* the move, so the caller
        can still archive it with a receipt.
        """
        ...

    def already_dispatched(self, uuid: str) -> bool:
        """True when either archive already holds this uuid."""
        ...

    def archive(self, uuid: str, receipt: DispatchReceipt) -> None:
        """Write the in-flight intent plus its receipt to the archive the
        receipt's outcome selects, then clear the in-flight entry. One call, so
        a caller cannot half-archive."""
        ...

    def inflight(self) -> list[str]:
        """Uuids claimed with no recorded outcome. Reported, never retried."""
        ...

    def receipt(self, uuid: str) -> DispatchReceipt | None: ...

    def dispatched_receipts(self) -> Iterator[DispatchReceipt]:
        """Every receipt with outcome `dispatched` -- what reconciliation walks."""
        ...

    def archived_intent(self, uuid: str) -> IntentEnvelope | None:
        """The archived intent, byte-identical to what was submitted.

        This is the body reconciliation diffs a sent message against. The
        implementation this replaces wrote a parallel `snapshots/` tree and
        carried its path in frontmatter to answer the same question; in an
        immutable archive the intent already *is* the snapshot.
        """
        ...

    def reconciled_verdict(self, uuid: str) -> str | None:
        """The settled verdict for a dispatched intent, or None while it is
        still open. Without it the reconciliation pass would re-fetch every
        draft it has ever sent on every run."""
        ...

    def mark_reconciled(self, uuid: str, verdict: str) -> None:
        """Record a terminal verdict. A receipt is immutable, so this is a
        second, smaller marker rather than an edit to the first."""
        ...


class InMemoryIntentStore:
    """Dicts, same semantics. Ships in the library, not bolted onto the tests.

    The semantics are the point, not the signatures: a second `claim` raises,
    `already_dispatched` flips only after `archive`, and `inflight()` reflects
    a claim with no archive. A fake that implemented only the happy path would
    make the parametrised suite prove nothing.
    """

    def __init__(self) -> None:
        self._pending: dict[str, tuple[str, str]] = {}
        self._inflight: dict[str, str] = {}
        self._archived: dict[str, str] = {}
        self._receipts: dict[str, DispatchReceipt] = {}
        self._verdicts: dict[str, str] = {}

    def put(self, outbox_name: str, uuid: str, content: str) -> None:
        self._pending[uuid] = (outbox_name, content)

    def pending(self) -> Iterator[tuple[str, str]]:
        for uuid, (outbox_name, _) in sorted(self._pending.items()):
            yield outbox_name, uuid

    def claim(self, outbox_name: str, uuid: str) -> IntentEnvelope:
        if uuid in self._inflight:
            raise IntentAlreadyClaimed(f"{uuid} is already in flight")
        entry = self._pending.pop(uuid, None)
        if entry is None or entry[0] != outbox_name:
            raise IntentAlreadyClaimed(f"{outbox_name}/{uuid} is not pending")
        self._inflight[uuid] = entry[1]
        return parse_intent(entry[1], f"{outbox_name}/{uuid}", uuid)

    def already_dispatched(self, uuid: str) -> bool:
        return uuid in self._archived

    def archive(self, uuid: str, receipt: DispatchReceipt) -> None:
        content = self._inflight.pop(uuid, None)
        if content is None:
            raise IntentNotClaimed(f"{uuid} is not in flight; claim it before archiving")
        self._archived[uuid] = content
        self._receipts[uuid] = receipt

    def inflight(self) -> list[str]:
        return sorted(self._inflight)

    def receipt(self, uuid: str) -> DispatchReceipt | None:
        return self._receipts.get(uuid)

    def dispatched_receipts(self) -> Iterator[DispatchReceipt]:
        for uuid in sorted(self._receipts):
            receipt = self._receipts[uuid]
            if receipt.outcome == "dispatched":
                yield receipt

    def archived_intent(self, uuid: str) -> IntentEnvelope | None:
        content = self._archived.get(uuid)
        if content is None:
            return None
        return parse_intent(content, f"archive/{uuid}", uuid)

    def reconciled_verdict(self, uuid: str) -> str | None:
        return self._verdicts.get(uuid)

    def mark_reconciled(self, uuid: str, verdict: str) -> None:
        self._verdicts[uuid] = verdict
