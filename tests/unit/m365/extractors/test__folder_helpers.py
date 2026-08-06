"""Tests for mail folder resolution and auto-discovery."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from m365_brain.m365.client import GraphApiError, GraphClient
from m365_brain.m365.extractors._folder_helpers import (
    cache_folder_id,
    list_all_folders,
    resolve_folder_id,
)


def _client(response: dict) -> MagicMock:
    client = MagicMock(spec=GraphClient)
    client.get.return_value = response
    return client


class TestResolveFolderId:
    def test_well_known_folder_needs_no_graph_call(self) -> None:
        client = _client({})
        cache: dict[tuple[str, str], str] = {}
        assert resolve_folder_id(client, "/me", "me", "SentItems", cache) == "SentItems"
        client.get.assert_not_called()
        assert cache == {}

    def test_custom_folder_is_resolved_then_cached(self) -> None:
        client = _client({"value": [{"id": "AAMk-custom-1", "displayName": "Projects"}]})
        cache: dict[tuple[str, str], str] = {}

        first = resolve_folder_id(client, "/users/a@x.test", "a@x.test", "Projects", cache)
        second = resolve_folder_id(client, "/users/a@x.test", "a@x.test", "Projects", cache)

        assert first == second == "AAMk-custom-1"
        assert cache == {("a@x.test", "Projects"): "AAMk-custom-1"}
        client.get.assert_called_once()
        assert client.get.call_args.args[0] == "/users/a@x.test/mailFolders"

    def test_apostrophe_in_name_is_odata_escaped(self) -> None:
        client = _client({"value": [{"id": "AAMk-obrien", "displayName": "O'Brien"}]})
        resolve_folder_id(client, "/me", "me", "O'Brien", {})
        params = client.get.call_args.args[1]
        assert params["$filter"] == "displayName eq 'O''Brien'"

    def test_missing_folder_raises_with_an_actionable_message(self) -> None:
        client = _client({"value": []})
        with pytest.raises(GraphApiError) as exc_info:
            resolve_folder_id(client, "/me", "me@x.test", "Nope", {})
        assert "Mail folder not found: 'Nope'" in str(exc_info.value)
        assert "mailbox=me@x.test" in str(exc_info.value)
        assert exc_info.value.status_code is None

    def test_primed_cache_short_circuits_the_graph_call(self) -> None:
        client = _client({"value": [{"id": "should-not-be-used"}]})
        cache: dict[tuple[str, str], str] = {}
        cache_folder_id(cache, "a@x.test", "Projects", "AAMk-primed")

        assert resolve_folder_id(client, "/users/a@x.test", "a@x.test", "Projects", cache) == "AAMk-primed"
        client.get.assert_not_called()


def _paging_client(pages: dict[str, list[dict]], max_pages: int = 10) -> MagicMock:
    """A client whose `get_pages` serves one page per requested path.

    `get_pages` rather than `get`, because a single `get` is half of what
    `folders: null` was silently losing.
    """
    client = MagicMock(spec=GraphClient)
    client.max_pages = max_pages
    client.get_pages.side_effect = lambda path, params, cap: (pages.get(path, []), False)
    # `get` is wired to the same pages so that reverting the traversal fails on
    # the assertion below rather than on a MagicMock -- the point of the guard
    # is what discovery returns, not how it asks.
    client.get.side_effect = lambda path, params: {"value": pages.get(path, [])}
    return client


class TestListAllFolders:
    def test_returns_only_user_folders(self) -> None:
        client = _paging_client(
            {
                "/me/mailFolders": [
                    {"id": "id-inbox", "displayName": "Inbox", "isHidden": False},
                    {"id": "id-projects", "displayName": "Projects"},
                    {"id": "id-drafts", "displayName": "Drafts"},
                    {"id": "id-junk", "displayName": "Junk Email"},
                    {"id": "id-hidden", "displayName": "Visible Name", "isHidden": True},
                    {"id": "id-noname", "displayName": ""},
                    {"displayName": "No Id"},
                ]
            }
        )
        assert list_all_folders(client, "/me", "me") == [("Inbox", "id-inbox"), ("Projects", "id-projects")]

    def test_requests_the_fields_the_filter_and_the_walk_depend_on(self) -> None:
        client = _paging_client({})
        assert list_all_folders(client, "/users/a@x.test", "a@x.test") == []
        path, params, cap = client.get_pages.call_args.args
        assert path == "/users/a@x.test/mailFolders"
        assert params["$select"] == "id,displayName,isHidden,childFolderCount"
        assert cap == client.max_pages, "the collection is paged under graph.max_pages"


class TestAutoDiscoveryReachesEveryVisibleFolder:
    """`folders: null` is documented as "all visible folders" and returned two thirds of nothing.

    `GET /mailFolders` returns only the root's children -- Microsoft's reference
    says so outright -- and this call was a bare `client.get` with a literal
    `$top=100` and no `nextLink` follow. So anything an operator filed one level
    down was never synced, never indexed, never triaged, and a mailbox with more
    than a hundred folders lost the tail. Neither loss said anything: the round
    completed and reported the folders it did find.
    """

    def test_a_nested_folder_is_discovered(self) -> None:
        client = _paging_client(
            {
                "/me/mailFolders": [
                    {"id": "id-inbox", "displayName": "Inbox", "isHidden": False, "childFolderCount": 2},
                ],
                "/me/mailFolders/id-inbox/childFolders": [
                    {"id": "id-2026", "displayName": "2026", "isHidden": False, "childFolderCount": 1},
                    {"id": "id-2025", "displayName": "2025", "isHidden": False, "childFolderCount": 0},
                ],
                "/me/mailFolders/id-2026/childFolders": [
                    {"id": "id-q1", "displayName": "Q1", "isHidden": False, "childFolderCount": 0},
                ],
            }
        )

        assert sorted(list_all_folders(client, "/me", "me")) == [
            ("2025", "id-2025"),
            ("2026", "id-2026"),
            ("Inbox", "id-inbox"),
            ("Q1", "id-q1"),
        ]

    def test_a_skipped_folder_is_not_descended_into(self) -> None:
        """The children of `Deleted Items` are deleted items."""
        client = _paging_client(
            {
                "/me/mailFolders": [
                    {"id": "id-bin", "displayName": "Deleted Items", "isHidden": False, "childFolderCount": 3},
                ],
                "/me/mailFolders/id-bin/childFolders": [
                    {"id": "id-old", "displayName": "Old", "isHidden": False, "childFolderCount": 0},
                ],
            }
        )

        assert list_all_folders(client, "/me", "me") == []
