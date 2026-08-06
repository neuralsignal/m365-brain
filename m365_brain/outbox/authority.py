"""Four permission levels, and the one transition table that reads them.

**Why `authority` and not `tier`.** This package already spends `tier` on a
different vocabulary one directory over: `ops/tiers.py` computes a *person's*
relationship rung -- `Tier 1`, `Tier 2`, `Tier 3` -- from interaction counts,
those names are written into person files, and three of the consuming
workspace's agents read them under that word. Two modules named `tiers.py` in
one package, and `outbox list --json` emitted `"tier": "draft_only"` beside a
corpus where `tier` meant `"Tier 1"`. The value sets cannot collide -- passing
one to the other raises -- but the *word* is what a reader and a JSON key
carry, and the person names are the ones written to disk 66 times over. So this
side moved: an outbox has an authority, a person has a tier.

The authority is a property of the *outbox*, resolved by name from config at
dispatch. It is never read from the intent file -- an author who could set
their own authority would have a permission system that grants itself
permissions.

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


class Authority(StrEnum):
    """How much autonomy an outbox has."""

    NEVER_AUTO = "never_auto"
    """Declared un-automatable. Exists so "we deliberately do not do this"
    is a config value rather than a missing handler nobody can find."""

    HUMAN_APPROVAL = "human_approval"
    DRAFT_ONLY = "draft_only"
    AUTO_SEND = "auto_send"


IntentStatus = Literal["pending", "approved", "dispatched", "failed", "blocked"]
"""An intent's own lifecycle. Its terminal members are exactly `DispatchOutcome`.

The fifth member used to be `rejected`, a word `outbox.reconcile.Verdict`
spends on the opposite fact -- see `vault/dispatch.py`."""

Action = Literal["await_admin", "await_approval", "execute", "archive"]

TERMINAL_STATUSES: frozenset[str] = frozenset({"dispatched", "failed", "blocked"})


class AuthorityRoutingError(Exception):
    """No transition is defined for this (authority, status). A programming error."""


class AuthorityViolation(Exception):
    """A handler or an auth profile contradicts its declared authority.

    Raised at registry build -- process start -- never per intent. A
    configuration that cannot be true is not data to be routed around.
    """


class AuthorityRouter:
    """Maps `(authority, status)` to the one action the runner may take."""

    def next_action(self, authority: Authority, status: IntentStatus) -> Action:
        """The action for this pair. Raises `AuthorityRoutingError` for any pair
        not in the table -- a missing transition is a bug, not an input."""
        if status in TERMINAL_STATUSES:
            return "archive"
        match (authority, status):
            case (Authority.NEVER_AUTO, "pending"):
                return "await_admin"
            case (Authority.HUMAN_APPROVAL, "pending"):
                return "await_approval"
            case (Authority.HUMAN_APPROVAL, "approved"):
                return "execute"
            # The corrected row. Do not "fix" this back to `archive`: a
            # draft_only outbox drafts, and drafting is the execution.
            case (Authority.DRAFT_ONLY, "pending"):
                return "execute"
            case (Authority.AUTO_SEND, "pending"):
                return "execute"
        raise AuthorityRoutingError(
            f"no transition defined for authority={authority.value!r} status={status!r}. "
            "An 'approved' status only means anything under human_approval, which is the "
            "only authority with an approval step."
        )
