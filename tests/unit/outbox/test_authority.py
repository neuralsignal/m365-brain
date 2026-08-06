"""The full transition table, including every combination that must raise.

The `(draft_only, pending) -> execute` case has its own test and its own
comment. It is the one row this port corrects, and a future reader comparing
against the source will find the source says `archive` -- so the reason it does
not has to be written down where the change is, not only in an ADR.
"""

from __future__ import annotations

import pytest

from m365_brain.outbox.authority import Authority, AuthorityRouter, AuthorityRoutingError

ROUTER = AuthorityRouter()


@pytest.mark.parametrize(
    ("authority", "status", "expected"),
    [
        (Authority.NEVER_AUTO, "pending", "await_admin"),
        (Authority.HUMAN_APPROVAL, "pending", "await_approval"),
        (Authority.HUMAN_APPROVAL, "approved", "execute"),
        (Authority.DRAFT_ONLY, "pending", "execute"),
        (Authority.AUTO_SEND, "pending", "execute"),
    ],
)
def test_the_transition_table(authority, status, expected):
    assert ROUTER.next_action(authority, status) == expected


@pytest.mark.parametrize("authority", list(Authority))
@pytest.mark.parametrize("status", ["dispatched", "failed", "blocked"])
def test_every_terminal_status_archives_whatever_the_authority(authority, status):
    assert ROUTER.next_action(authority, status) == "archive"


@pytest.mark.parametrize(
    "authority",
    [Authority.NEVER_AUTO, Authority.DRAFT_ONLY, Authority.AUTO_SEND],
)
def test_approved_raises_for_every_authority_without_an_approval_step(authority):
    """`approved` only means something under human_approval. Anywhere else it
    is a status nobody could have set, so it is a bug rather than an input."""
    with pytest.raises(AuthorityRoutingError):
        ROUTER.next_action(authority, "approved")


def test_an_unknown_status_raises_rather_than_defaulting():
    with pytest.raises(AuthorityRoutingError):
        ROUTER.next_action(Authority.HUMAN_APPROVAL, "invented")


def test_draft_only_pending_executes_and_this_is_deliberate():
    """DO NOT change this to `archive`.

    The implementation this ports mapped `(draft_only, pending) -> archive`.
    All three of its email outboxes were `draft_only`, so under its own router
    every email intent would have been filed away without a draft ever being
    created. Its module docstring, its design document and its ADR all say the
    drafting call executes; only the code disagreed.
    """
    assert ROUTER.next_action(Authority.DRAFT_ONLY, "pending") == "execute"


def test_the_tier_values_are_the_configured_vocabulary():
    """`Authority` and the config Literal are one vocabulary; a drift between them
    would make a valid config unroutable."""
    from m365_brain.config.outbox import Authority as ConfiguredAuthority

    assert set(ConfiguredAuthority.__args__) == {authority.value for authority in Authority}
