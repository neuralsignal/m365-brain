"""Four permission tiers, and the one transition table that reads them.

The tier is a property of the *outbox*, resolved by name from config at
dispatch. It is never read from the intent file -- an author who could set
their own tier would have a permission system that grants itself permissions.

One entry in the ported table is corrected here, and it is corrected loudly
because a silent correction is how a port loses behaviour. The source mapped
`(draft_only, pending) -> archive`. All three of its email outboxes are
`draft_only`, so under its own router every email intent would have been
archived without a draft ever being created. Its module docstring, its design
document and its ADR all say the drafting call executes; the code was the
outlier. See `docs/decisions/0013-draft-only-executes.md`.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal


class Tier(StrEnum):
    """How much autonomy an outbox has."""

    NEVER_AUTO = "never_auto"
    """Declared un-automatable. Exists so "we deliberately do not do this"
    is a config value rather than a missing handler nobody can find."""

    HUMAN_APPROVAL = "human_approval"
    DRAFT_ONLY = "draft_only"
    AUTO_SEND = "auto_send"


IntentStatus = Literal["pending", "approved", "dispatched", "rejected", "failed"]

Action = Literal["await_admin", "await_approval", "execute", "archive"]

TERMINAL_STATUSES: frozenset[str] = frozenset({"dispatched", "rejected", "failed"})


class TierRoutingError(Exception):
    """No transition is defined for this (tier, status). A programming error."""


class TierViolation(Exception):
    """A handler or an auth profile contradicts its declared tier.

    Raised at registry build -- process start -- never per intent. A
    configuration that cannot be true is not data to be routed around.
    """


class TierRouter:
    """Maps `(tier, status)` to the one action the runner may take."""

    def next_action(self, tier: Tier, status: IntentStatus) -> Action:
        """The action for this pair. Raises `TierRoutingError` for any pair not
        in the table -- a missing transition is a bug, not an input."""
        if status in TERMINAL_STATUSES:
            return "archive"
        match (tier, status):
            case (Tier.NEVER_AUTO, "pending"):
                return "await_admin"
            case (Tier.HUMAN_APPROVAL, "pending"):
                return "await_approval"
            case (Tier.HUMAN_APPROVAL, "approved"):
                return "execute"
            # The corrected row. Do not "fix" this back to `archive`: a
            # draft_only outbox drafts, and drafting is the execution.
            case (Tier.DRAFT_ONLY, "pending"):
                return "execute"
            case (Tier.AUTO_SEND, "pending"):
                return "execute"
        raise TierRoutingError(
            f"no transition defined for tier={tier.value!r} status={status!r}. "
            "An 'approved' status only means anything under human_approval, which is the "
            "only tier with an approval step."
        )
