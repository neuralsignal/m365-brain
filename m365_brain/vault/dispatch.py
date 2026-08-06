"""The vocabulary a dispatch speaks: declared before it runs, recorded after.

This sits in `vault` for the same reason `payloads` does, and the reason is
worth writing down because it is not obvious from the names.

`outbox` (the lifecycle: claim, route, archive) and `m365` (the executor: Graph
calls) are **peers** in the layer map -- neither may import the other, so that
the knowledge half of this package stays usable with Microsoft 365 absent
entirely. But a handler has to declare what it may do, and the tier guard has
to read that declaration, so the two need a shared vocabulary. `vault` is the
layer both may see, and the receipt is a vault artifact in any case: it is the
`.receipt.json` sidecar written beside the archived intent.

`OutboxHandler` is a Protocol, so an executor satisfies it structurally without
importing anything at all.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Final, Literal, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from m365_brain.vault.intent import IntentEnvelope


class GraphOp(StrEnum):
    """A side-effecting operation a handler may perform.

    Declared per handler and checked against the outbox's configured tier at
    startup. A runtime check that a handler "only drafts" is unfalsifiable; a
    static declaration compared against a static policy is not.
    """

    CREATE_DRAFT = "create_draft"
    UPDATE_DRAFT = "update_draft"
    ATTACH = "attach"
    SEND_MAIL = "send_mail"
    POST_CHANNEL = "post_channel_message"
    PUT_FILE = "put_file"


DRAFT_ONLY_OPS: Final[frozenset[GraphOp]] = frozenset({GraphOp.CREATE_DRAFT, GraphOp.UPDATE_DRAFT, GraphOp.ATTACH})
"""Everything a `draft_only` outbox is allowed to declare. Not config: an
operator widening this would be redefining what "draft only" means, which is
the one thing the tier exists to hold still."""


DispatchOutcome = Literal["dispatched", "failed", "blocked"]
"""What became of an intent we were asked to send. Disjoint from `Verdict`.

`failed` was `rejected`, and the two words were opposite facts under one uuid:
here it meant **we never dispatched it**, while `outbox.reconcile.Verdict`'s
`rejected` means **we did dispatch it and a human then deleted the draft**. An
operator grepping `_meta` for "rejected" got both, and the archive directory
holding only the first is called `_rejected`, which reinforced the wrong
reading. This side moved because the other is the human's own word for what
they did, and `ops triage` reads it back under that name.

`blocked` stays separate from `failed`: policy refused (the outbox's authority
tier), rather than an attempt that went wrong.
"""

NonDispatchReason = Literal[
    "tier_blocked",
    "no_approval_recorded",
    "etag_conflict",
    "graph_error",
    "attachment_missing",
    "parse_error",
    "unknown_outbox",
]
"""Why an intent was not dispatched -- `blocked` and `failed` both land here.

Closed set, machine-readable. An operator greps receipts by reason, so a
free-text field would make the archive unqueryable exactly when it matters.
Named for the whole set rather than for `rejected`, which is no longer one of
the outcomes it explains."""


class DispatchResult(BaseModel):
    """What a handler produced. Not `None`: a dispatch nobody can point at is
    a dispatch nobody can reconcile."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    graph_message_id: str | None


class DispatchReceipt(BaseModel):
    """The sidecar written beside an archived intent.

    A sidecar rather than injected frontmatter. The implementation this
    replaces wrote a `rejection_reason:` key into the archived file, which then
    failed its own `extra="forbid"` on any re-read -- a hazard they worked
    around by never re-reading the failed archive. Here the archived intent
    is byte-identical to what was submitted, which is what lets it serve as the
    fixture reconciliation compares against.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)
    uuid: str
    kind: str
    outcome: DispatchOutcome
    dispatched_at: datetime
    graph_message_id: str | None
    reason: NonDispatchReason | None
    detail: str | None
    """Human-readable context for `reason`. Set iff `reason` is set."""


@runtime_checkable
class OutboxHandler(Protocol):
    """One outbox's executor, with its transport and config already bound.

    Context is bound at construction rather than passed per call, so the runner
    never has to name a type that belongs to the executor's half of the world.
    """

    name: str
    declared_ops: frozenset[GraphOp]

    def execute(self, envelope: IntentEnvelope) -> DispatchResult: ...
