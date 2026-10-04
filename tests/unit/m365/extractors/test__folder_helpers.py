"""Tests for mail folder resolution and auto-discovery."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from m365_brain.m365.client import GraphApiError, GraphClient
from m365_brain.m365.extractors._folder_helpers import list_all_folders, resolve_folder_id


def _client(response: dict) -> MagicMock:
    client = MagicMock(spec=GraphClient)
    client.get.return_value = response
    return client


class TestResolveFolderId:
    def test_well_known_alias_resolves_to_the_real_id(self) -> None:
        """`Inbox` is an alias; state keyed by it would not match the discovered folder."""
        client = _client({"id": "AAMk-inbox", "displayName": "Posteingang"})
        assert resolve_folder_id(client, "/users/a@x.test", "a@x.test", "SentItems") == "AAMk-inbox"
        client.get.assert_called_once_with("/users/a@x.test/mailFolders/SentItems", {"$select": "id,displayName"})

    def test_custom_folder_is_resolved_by_display_name(self) -> None:
        client = _client({"value": [{"id": "AAMk-custom-1", "displayName": "Projects"}]})
        assert resolve_folder_id(client, "/users/a@x.test", "a@x.test", "Projects") == "AAMk-custom-1"
        assert client.get.call_args.args[0] == "/users/a@x.test/mailFolders"

    def test_apostrophe_in_name_is_odata_escaped(self) -> None:
        client = _client({"value": [{"id": "AAMk-obrien", "displayName": "O'Brien"}]})
        resolve_folder_id(client, "/me", "me", "O'Brien")
        params = client.get.call_args.args[1]
        assert params["$filter"] == "displayName eq 'O''Brien'"

    def test_missing_folder_raises_with_an_actionable_message(self) -> None:
        client = _client({"value": []})
        with pytest.raises(GraphApiError) as exc_info:
            resolve_folder_id(client, "/me", "me@x.test", "Nope")
        assert "Mail folder not found: 'Nope'" in str(exc_info.value)
        assert "mailbox=me@x.test" in str(exc_info.value)
        assert exc_info.value.status_code is None


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
        assert list_all_folders(client, "/me", "me")[0] == [("Inbox", "id-inbox"), ("Projects", "id-projects")]

    def test_requests_the_fields_the_filter_and_the_walk_depend_on(self) -> None:
        client = _paging_client({})
        assert list_all_folders(client, "/users/a@x.test", "a@x.test")[0] == []
        path, params, cap = client.get_pages.call_args.args
        assert path == "/users/a@x.test/mailFolders"
        assert params["$select"] == "id,displayName,isHidden,childFolderCount"
        assert cap == client.max_pages, "the collection is paged under graph.max_pages"


class TestDiscoveryReportsTruncation:
    def test_a_truncated_page_walk_is_reported(self) -> None:
        client = _paging_client({})
        client.get_pages.side_effect = lambda path, params, cap: ([{"id": "id-p", "displayName": "Projects"}], True)
        assert list_all_folders(client, "/me", "me") == ([("Projects", "id-p")], True)

    def test_a_complete_walk_is_not_truncated(self) -> None:
        client = _paging_client({"/me/mailFolders": [{"id": "id-p", "displayName": "Projects"}]})
        assert list_all_folders(client, "/me", "me") == ([("Projects", "id-p")], False)


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

        assert sorted(list_all_folders(client, "/me", "me")[0]) == [
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

        assert list_all_folders(client, "/me", "me")[0] == []


class TestGermanMailboxAndDuplicates:
    def test_german_system_folders_are_skipped(self) -> None:
        """A de-DE mailbox names its system folders in German; `Gelöschte Elemente` holds deleted mail."""
        client = _paging_client(
            {
                "/me/mailFolders": [
                    {"id": "id-in", "displayName": "Posteingang"},
                    {"id": "id-bin", "displayName": "Gelöschte Elemente", "childFolderCount": 1},
                    {"id": "id-drafts", "displayName": "Entwürfe"},
                    {"id": "id-junk", "displayName": "Junk-E-Mail"},
                    {"id": "id-out", "displayName": "Postausgang"},
                    {"id": "id-sync", "displayName": "Synchronisierungsprobleme", "childFolderCount": 1},
                    {"id": "id-arch", "displayName": "Archiv", "childFolderCount": 1},
                ],
                "/me/mailFolders/id-bin/childFolders": [{"id": "id-x", "displayName": "Weg"}],
                "/me/mailFolders/id-sync/childFolders": [{"id": "id-c", "displayName": "Konflikte"}],
                "/me/mailFolders/id-arch/childFolders": [{"id": "id-t", "displayName": "Data & platform"}],
            }
        )
        assert sorted(list_all_folders(client, "/me", "me")[0]) == [
            ("Archiv", "id-arch"),
            ("Data & platform", "id-t"),
            ("Posteingang", "id-in"),
        ]

    def test_duplicate_display_names_are_both_returned(self) -> None:
        """Same-named folders are distinct folders; sync state is keyed by ID."""
        client = _paging_client(
            {
                "/me/mailFolders": [
                    {"id": "id-a", "displayName": "Archiv", "childFolderCount": 1},
                    {"id": "id-r", "displayName": "Receipts"},
                ],
                "/me/mailFolders/id-a/childFolders": [{"id": "id-r2", "displayName": "Receipts"}],
            }
        )
        assert sorted(list_all_folders(client, "/me", "me")[0]) == [
            ("Archiv", "id-a"),
            ("Receipts", "id-r"),
            ("Receipts", "id-r2"),
        ]
