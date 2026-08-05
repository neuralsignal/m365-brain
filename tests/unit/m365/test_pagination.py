"""Paging loops, exercised without HTTP.

Splitting these out of the client made them testable against a plain dict-
returning callable, which is the point: the interesting behaviour is what
happens at the page cap, and that has nothing to do with transport. Two
properties matter and both are easy to lose in a refactor -- `params` are sent
on the first page only (Graph bakes them into the nextLink), and hitting the
cap must report itself rather than look like a complete fetch.
"""

from __future__ import annotations

from typing import Any

from m365_brain.m365.pagination import fetch_delta, fetch_pages

NEXT = "@odata.nextLink"
DELTA = "@odata.deltaLink"


class RecordingFetch:
    """A `Fetch` that replays canned pages and records every (url, params) call."""

    def __init__(self, pages: list[dict]) -> None:
        self._pages = pages
        self.calls: list[tuple[str, dict[str, Any] | None]] = []

    def __call__(self, url: str, params: dict[str, Any] | None) -> dict:
        self.calls.append((url, params))
        return self._pages[len(self.calls) - 1]


def test_single_page_is_not_truncated():
    fetch = RecordingFetch([{"value": [{"id": "a"}, {"id": "b"}]}])

    items, truncated = fetch_pages(fetch, "/me/messages", {"$top": "50"}, 10)

    assert [i["id"] for i in items] == ["a", "b"]
    assert truncated is False


def test_follows_next_link_across_pages():
    fetch = RecordingFetch(
        [
            {"value": [{"id": "a"}], NEXT: "https://graph.example/page2"},
            {"value": [{"id": "b"}]},
        ]
    )

    items, truncated = fetch_pages(fetch, "/me/messages", None, 10)

    assert [i["id"] for i in items] == ["a", "b"]
    assert truncated is False
    assert [url for url, _ in fetch.calls] == ["/me/messages", "https://graph.example/page2"]


def test_params_are_sent_on_the_first_page_only():
    """Graph bakes the query into the nextLink; re-sending it is a 400 waiting to happen."""
    fetch = RecordingFetch(
        [
            {"value": [], NEXT: "https://graph.example/page2"},
            {"value": []},
        ]
    )

    fetch_pages(fetch, "/me/messages", {"$top": "50"}, 10)

    assert [params for _, params in fetch.calls] == [{"$top": "50"}, None]


def test_truncated_when_a_next_link_survives_the_cap():
    fetch = RecordingFetch([{"value": [{"id": "a"}], NEXT: "https://graph.example/page2"}])

    items, truncated = fetch_pages(fetch, "/me/messages", None, 1)

    assert len(items) == 1
    assert truncated is True
    assert len(fetch.calls) == 1


def test_delta_captures_the_delta_link():
    fetch = RecordingFetch([{"value": [{"id": "a"}], DELTA: "https://graph.example/delta?token=1"}])

    items, resume = fetch_delta(fetch, "/me/messages/delta", None, {"$top": "50"}, 10)

    assert [i["id"] for i in items] == ["a"]
    assert resume == "https://graph.example/delta?token=1"


def test_delta_resumes_from_the_stored_link_and_drops_params():
    """A resume link already carries the query -- re-sending params would double it."""
    fetch = RecordingFetch([{"value": [], DELTA: "https://graph.example/delta?token=2"}])

    fetch_delta(fetch, "/me/messages/delta", "https://graph.example/delta?token=1", {"$top": "50"}, 10)

    assert fetch.calls == [("https://graph.example/delta?token=1", None)]


def test_delta_returns_the_pending_next_link_as_the_resume_point():
    """At the cap the resume link is the nextLink, not the (absent) deltaLink.

    Returning the deltaLink here would silently skip everything past the cap;
    returning None would restart the whole delta round next cycle.
    """
    fetch = RecordingFetch([{"value": [{"id": "a"}], NEXT: "https://graph.example/page2"}])

    items, resume = fetch_delta(fetch, "/me/messages/delta", None, None, 1)

    assert len(items) == 1
    assert resume == "https://graph.example/page2"


def test_delta_link_from_the_last_page_wins_over_earlier_pages():
    fetch = RecordingFetch(
        [
            {"value": [{"id": "a"}], NEXT: "https://graph.example/page2"},
            {"value": [{"id": "b"}], DELTA: "https://graph.example/delta?token=final"},
        ]
    )

    _, resume = fetch_delta(fetch, "/me/messages/delta", None, None, 10)

    assert resume == "https://graph.example/delta?token=final"


def test_empty_collection_yields_no_items():
    fetch = RecordingFetch([{"value": []}])

    items, truncated = fetch_pages(fetch, "/me/messages", None, 10)

    assert items == []
    assert truncated is False
