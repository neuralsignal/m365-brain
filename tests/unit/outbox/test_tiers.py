"""The full transition table, including every combination that must raise.

The `(draft_only, pending) -> execute` case has its own test and its own
comment. It is the one row this port corrects, and a future reader comparing
against the source will find the source says `archive` -- so the reason it does
not has to be written down where the change is, not only in an ADR.
"""

from __future__ import annotations

import pytest

from m365_brain.outbox.tiers import Tier, TierRouter, TierRoutingError

ROUTER = TierRouter()


@pytest.mark.parametrize(
    ("tier", "status", "expected"),
    [
        (Tier.NEVER_AUTO, "pending", "await_admin"),
        (Tier.HUMAN_APPROVAL, "pending", "await_approval"),
        (Tier.HUMAN_APPROVAL, "approved", "execute"),
        (Tier.DRAFT_ONLY, "pending", "execute"),
        (Tier.AUTO_SEND, "pending", "execute"),
    ],
)
def test_the_transition_table(tier, status, expected):
    assert ROUTER.next_action(tier, status) == expected


@pytest.mark.parametrize("tier", list(Tier))
@pytest.mark.parametrize("status", ["dispatched", "rejected", "failed"])
def test_every_terminal_status_archives_whatever_the_tier(tier, status):
    assert ROUTER.next_action(tier, status) == "archive"


@pytest.mark.parametrize(
    "tier",
    [Tier.NEVER_AUTO, Tier.DRAFT_ONLY, Tier.AUTO_SEND],
)
def test_approved_raises_for_every_tier_without_an_approval_step(tier):
    """`approved` only means something under human_approval. Anywhere else it
    is a status nobody could have set, so it is a bug rather than an input."""
    with pytest.raises(TierRoutingError):
        ROUTER.next_action(tier, "approved")


def test_an_unknown_status_raises_rather_than_defaulting():
    with pytest.raises(TierRoutingError):
        ROUTER.next_action(Tier.HUMAN_APPROVAL, "invented")


def test_draft_only_pending_executes_and_this_is_deliberate():
    """DO NOT change this to `archive`.

    The implementation this ports mapped `(draft_only, pending) -> archive`.
    All three of its email outboxes were `draft_only`, so under its own router
    every email intent would have been filed away without a draft ever being
    created. Its module docstring, its design document and its ADR all say the
    drafting call executes; only the code disagreed.
    """
    assert ROUTER.next_action(Tier.DRAFT_ONLY, "pending") == "execute"


def test_the_tier_values_are_the_configured_vocabulary():
    """`Tier` and the config Literal are one vocabulary; a drift between them
    would make a valid config unroutable."""
    from m365_brain.config.outbox import Tier as ConfiguredTier

    assert set(ConfiguredTier.__args__) == {tier.value for tier in Tier}
