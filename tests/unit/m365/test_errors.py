"""The exception hierarchy is load-bearing, so it gets pinned.

`GraphNotFoundError` and `GraphConflictError` were added when the write half of
the transport was folded in. They subclass `GraphApiError` for exactly one
reason: ten `except GraphApiError` sites in the extractors had to keep working
without being touched. A future "tidy-up" that reparents either of them would
silently stop those handlers catching a 404, so the relationship is asserted
here rather than left to the class statement.
"""

from __future__ import annotations

import pytest

from m365_brain.m365 import client as client_module
from m365_brain.m365.errors import GraphApiError, GraphConflictError, GraphNotFoundError

SUBCLASSES = (GraphNotFoundError, GraphConflictError)


@pytest.mark.parametrize("subclass", SUBCLASSES)
def test_subclasses_are_caught_by_except_graph_api_error(subclass):
    with pytest.raises(GraphApiError):
        raise subclass("boom", 404)


@pytest.mark.parametrize("subclass", SUBCLASSES)
def test_subclasses_carry_the_status_code(subclass):
    exc = subclass("boom", 412)
    assert exc.status_code == 412


@pytest.mark.parametrize("subclass", SUBCLASSES)
def test_a_specific_handler_does_not_swallow_the_sibling(subclass):
    """Catching one subclass must not catch the other -- they mean different things."""
    other = next(s for s in SUBCLASSES if s is not subclass)
    with pytest.raises(other):
        try:
            raise other("boom", 400)
        except subclass:  # pragma: no cover - the point is that this never fires
            pytest.fail(f"{subclass.__name__} caught a {other.__name__}")


def test_status_code_may_be_none_for_a_logical_failure():
    """A blocked download URL is a failure with no HTTP response behind it."""
    assert GraphApiError("blocked", None).status_code is None


@pytest.mark.parametrize("name", ["GraphApiError", "GraphNotFoundError", "GraphConflictError"])
def test_client_re_exports_every_exception(name):
    """`from m365_brain.m365.client import GraphApiError` is what the extractors write."""
    assert getattr(client_module, name) is globals()[name]
