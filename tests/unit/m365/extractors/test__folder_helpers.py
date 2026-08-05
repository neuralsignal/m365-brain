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


class TestListAllFolders:
    def test_returns_only_user_folders(self) -> None:
        client = _client(
            {
                "value": [
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

    def test_requests_the_fields_the_filter_depends_on(self) -> None:
        client = _client({"value": []})
        assert list_all_folders(client, "/users/a@x.test", "a@x.test") == []
        path, params = client.get.call_args.args
        assert path == "/users/a@x.test/mailFolders"
        assert params["$select"] == "id,displayName,isHidden"
