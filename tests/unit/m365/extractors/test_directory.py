"""Tests for directory extractor."""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import MagicMock
from urllib.parse import unquote_plus

import pytest
from pytest_httpx import HTTPXMock

from m365_brain.config import DirectoryExtractorConfig, GraphConfig
from m365_brain.config.index import RelationConfig
from m365_brain.m365.client import GraphApiError, GraphClient
from m365_brain.m365.extractors import directory
from m365_brain.m365.frontmatter import MANAGER
from m365_brain.m365.markdown_writer import loads_markdown, short_hash, slugify
from m365_brain.parsers.relations import parse_relations
from m365_brain.storage.local import LocalBackend
from m365_brain.vault.removal import PATH_MAP_STATE_KEY

FIXTURES_DIR = Path(__file__).resolve().parents[3] / "fixtures"


@pytest.fixture()
def directory_config():
    return DirectoryExtractorConfig(
        enabled=True,
        poll_interval_minutes=10080,
        include_manager_chain=False,
        include_direct_reports=False,
        only_active_users=True,
    )


@pytest.fixture()
def graph_config():
    return GraphConfig(
        max_retries=1,
        backoff_base_ms=10,
        timeout_seconds=5,
        max_pages=10,
        max_retry_after_seconds=300.0,
        error_message_max_length=200,
    )


@pytest.fixture()
def directory_response():
    return json.loads((FIXTURES_DIR / "directory_response.json").read_text())


class TestDirectoryExtractor:
    def test_sync_produces_markdown(
        self, httpx_mock: HTTPXMock, tmp_path, graph_config, directory_config, directory_response, ctx
    ):
        httpx_mock.add_response(
            url=re.compile(r".*/users/delta.*"),
            json=directory_response,
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        state, count = directory.run(client, storage, {}, directory_config, ctx)

        assert count == 2
        assert "delta_link" in state
        assert "last_sync" in state

        files = storage.list_files(ctx.paths.inbox_root("directory"))
        assert len(files) == 2

        client.close()

    def test_incremental_sync_uses_delta_link(
        self, httpx_mock: HTTPXMock, tmp_path, graph_config, directory_config, ctx
    ):
        delta_url = "https://graph.microsoft.com/v1.0/users/delta?$deltatoken=existing"
        httpx_mock.add_response(
            url=delta_url,
            json={
                "value": [
                    {
                        "id": "user-new",
                        "displayName": "New User",
                        "mail": "new@contoso.com",
                        "userPrincipalName": "new@contoso.com",
                        "jobTitle": "",
                        "department": "",
                        "officeLocation": "",
                        "city": "",
                        "accountEnabled": True,
                    }
                ],
                "@odata.deltaLink": "https://graph.microsoft.com/v1.0/users/delta?$deltatoken=new",
            },
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        existing_state = {"delta_link": delta_url}
        state, count = directory.run(client, storage, existing_state, directory_config, ctx)

        assert count == 1
        assert state["delta_link"] == "https://graph.microsoft.com/v1.0/users/delta?$deltatoken=new"
        client.close()

    def test_delta_query_sends_no_top(
        self, httpx_mock: HTTPXMock, tmp_path, graph_config, directory_config, directory_response, ctx
    ):
        """Regression: $top caps a delta enumeration in total, so the constant 50 capped the directory at 50 users."""
        httpx_mock.add_response(url=re.compile(r".*/users/delta.*"), json=directory_response)

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        directory.run(client, storage, {}, directory_config, ctx)

        assert "$top" not in httpx_mock.get_request().url.params
        client.close()

    def test_empty_response(self, httpx_mock: HTTPXMock, tmp_path, graph_config, directory_config, ctx):
        httpx_mock.add_response(
            url=re.compile(r".*/users/delta.*"),
            json={"value": [], "@odata.deltaLink": "https://delta?token=empty"},
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        state, count = directory.run(client, storage, {}, directory_config, ctx)
        assert count == 0
        client.close()

    def test_skips_users_without_display_name(
        self, httpx_mock: HTTPXMock, tmp_path, graph_config, directory_config, ctx
    ):
        httpx_mock.add_response(
            url=re.compile(r".*/users/delta.*"),
            json={
                "value": [
                    {"id": "no-name", "displayName": "", "accountEnabled": True},
                    {"id": "", "displayName": "No ID", "accountEnabled": True},
                ],
                "@odata.deltaLink": "https://delta?token=skip",
            },
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        state, count = directory.run(client, storage, {}, directory_config, ctx)
        assert count == 0
        client.close()

    def test_skips_disabled_users_when_only_active(self, httpx_mock: HTTPXMock, tmp_path, graph_config, ctx):
        config = DirectoryExtractorConfig(
            enabled=True,
            poll_interval_minutes=10080,
            include_manager_chain=False,
            include_direct_reports=False,
            only_active_users=True,
        )

        httpx_mock.add_response(
            url=re.compile(r".*/users/delta.*"),
            json={
                "value": [
                    {
                        "id": "active-user",
                        "displayName": "Active User",
                        "mail": "active@contoso.com",
                        "userPrincipalName": "active@contoso.com",
                        "accountEnabled": True,
                    },
                    {
                        "id": "disabled-user",
                        "displayName": "Disabled User",
                        "mail": "disabled@contoso.com",
                        "userPrincipalName": "disabled@contoso.com",
                        "accountEnabled": False,
                    },
                ],
                "@odata.deltaLink": "https://delta?token=active",
            },
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        state, count = directory.run(client, storage, {}, config, ctx)
        assert count == 1
        client.close()

    def test_includes_disabled_users_when_not_filtering(self, httpx_mock: HTTPXMock, tmp_path, graph_config, ctx):
        config = DirectoryExtractorConfig(
            enabled=True,
            poll_interval_minutes=10080,
            include_manager_chain=False,
            include_direct_reports=False,
            only_active_users=False,
        )

        httpx_mock.add_response(
            url=re.compile(r".*/users/delta.*"),
            json={
                "value": [
                    {
                        "id": "active-user",
                        "displayName": "Active User",
                        "mail": "active@contoso.com",
                        "userPrincipalName": "active@contoso.com",
                        "accountEnabled": True,
                    },
                    {
                        "id": "disabled-user",
                        "displayName": "Disabled User",
                        "mail": "disabled@contoso.com",
                        "userPrincipalName": "disabled@contoso.com",
                        "accountEnabled": False,
                    },
                ],
                "@odata.deltaLink": "https://delta?token=all",
            },
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        state, count = directory.run(client, storage, {}, config, ctx)
        assert count == 2
        client.close()

    def test_user_markdown_content(
        self, httpx_mock: HTTPXMock, tmp_path, graph_config, directory_config, directory_response, ctx
    ):
        httpx_mock.add_response(
            url=re.compile(r".*/users/delta.*"),
            json=directory_response,
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        directory.run(client, storage, {}, directory_config, ctx)

        files = storage.list_files(ctx.paths.inbox_root("directory"))
        content = storage.read_file(files[0])
        meta, body = loads_markdown(content)

        assert meta["type"] == "directory_user"
        assert meta["source"]["service"] == "directory"
        assert meta["source"]["extractor"] == "m365-brain/directory/1.0"
        assert "# " in body
        client.close()

    def test_with_manager_and_reports(self, httpx_mock: HTTPXMock, tmp_path, graph_config, ctx):
        config = DirectoryExtractorConfig(
            enabled=True,
            poll_interval_minutes=10080,
            include_manager_chain=True,
            include_direct_reports=True,
            only_active_users=False,
        )

        httpx_mock.add_response(
            url=re.compile(r".*/users/delta.*"),
            json={
                "value": [
                    {
                        "id": "user-mgr",
                        "displayName": "Manager User",
                        "mail": "mgr@contoso.com",
                        "userPrincipalName": "mgr@contoso.com",
                        "accountEnabled": True,
                    },
                ],
                "@odata.deltaLink": "https://delta?token=mgr",
            },
        )

        # Manager endpoint
        httpx_mock.add_response(
            url=re.compile(r".*/users/user-mgr/manager.*"),
            json={
                "id": "boss-001",
                "displayName": "The Boss",
            },
        )

        # Direct reports endpoint
        httpx_mock.add_response(
            url=re.compile(r".*/users/user-mgr/directReports.*"),
            json={
                "value": [
                    {"id": "report-001", "displayName": "Report One"},
                    {"id": "report-002", "displayName": "Report Two"},
                ],
            },
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        state, count = directory.run(client, storage, {}, config, ctx)
        assert count == 1

        files = storage.list_files(ctx.paths.inbox_root("directory"))
        content = storage.read_file(files[0])
        meta, body = loads_markdown(content)

        assert "manager" in meta
        assert "[[directory-the-boss-" in meta["manager"]
        assert len(meta["direct_reports"]) == 2
        assert "Organization" in body
        client.close()

    def test_manager_not_found_handled_gracefully(self, httpx_mock: HTTPXMock, tmp_path, graph_config, ctx):
        config = DirectoryExtractorConfig(
            enabled=True,
            poll_interval_minutes=10080,
            include_manager_chain=True,
            include_direct_reports=False,
            only_active_users=False,
        )

        httpx_mock.add_response(
            url=re.compile(r".*/users/delta.*"),
            json={
                "value": [
                    {
                        "id": "user-no-mgr",
                        "displayName": "Top Level User",
                        "mail": "top@contoso.com",
                        "userPrincipalName": "top@contoso.com",
                        "accountEnabled": True,
                    },
                ],
                "@odata.deltaLink": "https://delta?token=no-mgr",
            },
        )

        # Manager endpoint returns 404
        httpx_mock.add_response(
            url=re.compile(r".*/users/user-no-mgr/manager.*"),
            status_code=404,
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        state, count = directory.run(client, storage, {}, config, ctx)
        assert count == 1

        files = storage.list_files(ctx.paths.inbox_root("directory"))
        content = storage.read_file(files[0])
        meta, _ = loads_markdown(content)

        assert "manager" not in meta
        client.close()

    def test_build_user_link_missing_display_name(self):
        result = directory._build_user_link({"id": "abc"})
        assert result == ""

    def test_build_user_link_missing_id(self):
        result = directory._build_user_link({"displayName": "Alice"})
        assert result == ""

    def test_fetch_direct_reports_links_graph_api_error(self):
        client = MagicMock(spec=GraphClient)
        client.get_paginated.side_effect = GraphApiError("not found", 404)
        client.max_pages = 10

        result = directory._fetch_direct_reports_links(client, "user-123")

        assert result == []
        client.get_paginated.assert_called_once()


class TestExtractUserData:
    """Tests for _extract_user_data pure extraction function."""

    def test_extracts_full_user(self):
        user = {
            "id": "u-001",
            "displayName": "John Smith",
            "mail": "john@contoso.com",
            "userPrincipalName": "john@contoso.com",
            "jobTitle": "Senior Dev",
            "department": "Engineering",
            "officeLocation": "Building A",
            "city": "Seattle",
        }

        data = directory._extract_user_data(user, "[[manager-link]]", ["[[report-1]]"])

        assert data.user_id == "u-001"
        assert data.display_name == "John Smith"
        assert data.email == "john@contoso.com"
        assert data.upn == "john@contoso.com"
        assert data.job_title == "Senior Dev"
        assert data.department == "Engineering"
        assert data.office == "Building A"
        assert data.city == "Seattle"
        assert data.manager_link == "[[manager-link]]"
        assert data.direct_reports_links == ["[[report-1]]"]

    def test_handles_missing_fields(self):
        user = {"id": "u-002", "displayName": "Minimal User"}

        data = directory._extract_user_data(user, "", [])

        assert data.user_id == "u-002"
        assert data.display_name == "Minimal User"
        assert data.email == ""
        assert data.job_title == ""
        assert data.department == ""
        assert data.office == ""
        assert data.city == ""
        assert data.manager_link == ""
        assert data.direct_reports_links == []

    def test_none_values_become_empty_strings(self):
        user = {
            "id": "u-003",
            "displayName": "Null Fields",
            "mail": None,
            "jobTitle": None,
            "department": None,
            "officeLocation": None,
            "city": None,
        }

        data = directory._extract_user_data(user, "", [])

        assert data.email == ""
        assert data.job_title == ""
        assert data.department == ""
        assert data.office == ""
        assert data.city == ""


class TestOrgRelations:
    """What the extractor wrote, read back by the real relation parser.

    A test that asserted on the string the producer was handed cannot see this
    defect: `- **Manager:** [[X]]` is a perfectly good line until
    `parse_relations` reads everything before the wikilink as the edge's
    *type*, and only the parser can say what type came out.
    """

    def test_manager_edge_is_typed_with_a_bare_token(self, httpx_mock: HTTPXMock, tmp_path, graph_config, ctx):
        config = DirectoryExtractorConfig(
            enabled=True,
            poll_interval_minutes=10080,
            include_manager_chain=True,
            include_direct_reports=True,
            only_active_users=False,
        )
        httpx_mock.add_response(
            url=re.compile(r".*/users/delta.*"),
            json={
                "value": [
                    {
                        "id": "user-mgr",
                        "displayName": "Manager User",
                        "mail": "mgr@contoso.com",
                        "userPrincipalName": "mgr@contoso.com",
                        "accountEnabled": True,
                    }
                ],
                "@odata.deltaLink": "https://delta?token=rel",
            },
        )
        httpx_mock.add_response(
            url=re.compile(r".*/users/user-mgr/manager.*"),
            json={"id": "boss-001", "displayName": "The Boss"},
        )
        httpx_mock.add_response(
            url=re.compile(r".*/users/user-mgr/directReports.*"),
            json={"value": [{"id": "report-001", "displayName": "Report One"}]},
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")
        directory.run(client, storage, {}, config, ctx)

        content = storage.read_file(storage.list_files(ctx.paths.inbox_root("directory"))[0])
        parsed = parse_relations(content, RelationConfig(explicit_default_type="relates_to", inline_type="links_to"))
        boss = f"directory-{slugify('The Boss', 80)}-{short_hash('boss-001', 6)}"

        assert (MANAGER, boss) in [(edge.relation_type, edge.to_name) for edge in parsed]
        # The property, not just this one word: a config spells a bare token, so
        # a type carrying markup is a type no config can ever name.
        assert all(edge.relation_type.isidentifier() for edge in parsed), [e.relation_type for e in parsed]
        client.close()

    def test_direct_reports_remain_untyped(self, httpx_mock: HTTPXMock, tmp_path, graph_config, ctx):
        """Stated, not assumed. The sub-bullets still parse as `relates_to`.

        Left that way deliberately -- see CONTRACTS.md. This asserts the gap so
        that whoever adds a reader finds it here rather than in an empty report.
        """
        config = DirectoryExtractorConfig(
            enabled=True,
            poll_interval_minutes=10080,
            include_manager_chain=False,
            include_direct_reports=True,
            only_active_users=False,
        )
        httpx_mock.add_response(
            url=re.compile(r".*/users/delta.*"),
            json={
                "value": [
                    {
                        "id": "user-lead",
                        "displayName": "Lead User",
                        "mail": "lead@contoso.com",
                        "userPrincipalName": "lead@contoso.com",
                        "accountEnabled": True,
                    }
                ],
                "@odata.deltaLink": "https://delta?token=reports",
            },
        )
        httpx_mock.add_response(
            url=re.compile(r".*/users/user-lead/directReports.*"),
            json={"value": [{"id": "report-001", "displayName": "Report One"}]},
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")
        directory.run(client, storage, {}, config, ctx)

        content = storage.read_file(storage.list_files(ctx.paths.inbox_root("directory"))[0])
        parsed = parse_relations(content, RelationConfig(explicit_default_type="relates_to", inline_type="links_to"))

        assert [edge.relation_type for edge in parsed] == ["relates_to"]
        client.close()


def _flippable_user(enabled: bool, token: str) -> dict:
    return {
        "value": [
            {
                "id": "user-flip",
                "displayName": "Flipping User",
                "mail": "flip@contoso.com",
                "userPrincipalName": "flip@contoso.com",
                "accountEnabled": enabled,
            }
        ],
        "@odata.deltaLink": f"https://graph.microsoft.com/v1.0/users/delta?$deltatoken={token}",
    }


class TestDisabledAccountRemoval:
    """A disabled account is a removal, which is why the server-side filter is gone."""

    def test_no_server_side_account_enabled_filter(
        self, httpx_mock: HTTPXMock, local_storage, graph_config, directory_config, ctx
    ):
        """`$filter=accountEnabled eq true` must be ABSENT even with only_active_users.

        Filtering server-side made a disabled account vanish from the results
        rather than arrive flagged, so the flip was undetectable by construction
        and the user's page stayed in the vault forever. The flag is read
        client-side instead, and dropping the filter is the only thing that makes
        removal reachable for this extractor at all.
        """
        assert directory_config.only_active_users is True
        httpx_mock.add_response(
            url=re.compile(r".*/users/delta.*"),
            json={"value": [], "@odata.deltaLink": "https://graph.microsoft.com/v1.0/users/delta?$deltatoken=nf"},
        )
        client = GraphClient(graph_config, lambda: "test-token")

        directory.run(client, local_storage, {}, directory_config, ctx)

        request = httpx_mock.get_request()
        assert request is not None
        assert "$filter" not in unquote_plus(str(request.url))
        client.close()

    def test_disabling_an_account_deletes_its_page_and_repeating_it_is_a_noop(
        self, httpx_mock: HTTPXMock, local_storage, graph_config, directory_config, ctx
    ):
        client = GraphClient(graph_config, lambda: "test-token")
        inbox = ctx.paths.inbox_root("directory")

        httpx_mock.add_response(url=re.compile(r".*/users/delta.*"), json=_flippable_user(True, "t1"))
        state, count = directory.run(client, local_storage, {}, directory_config, ctx)
        assert count == 1
        # path_map records the item DIRECTORY, not the entry file, so that a
        # removal takes everything written beneath it.
        item_dir = state[PATH_MAP_STATE_KEY]["user-flip"]
        assert local_storage.list_files(inbox) == [ctx.paths.entry_file(item_dir)]

        httpx_mock.add_response(url=re.compile(r".*/users/delta.*"), json=_flippable_user(False, "t2"))
        state, count = directory.run(client, local_storage, state, directory_config, ctx)
        assert count == 0
        assert state[PATH_MAP_STATE_KEY] == {}
        assert local_storage.list_files(inbox) == []

        # A delta round that re-sends the same disabled user must not resurrect
        # the page, raise, or count anything.
        httpx_mock.add_response(url=re.compile(r".*/users/delta.*"), json=_flippable_user(False, "t3"))
        state, count = directory.run(client, local_storage, state, directory_config, ctx)
        assert count == 0
        assert state[PATH_MAP_STATE_KEY] == {}
        assert local_storage.list_files(inbox) == []
        client.close()


class TestOddLayoutGoldenPaths:
    def test_users_land_under_the_configured_names_only(
        self, httpx_mock: HTTPXMock, local_storage, graph_config, directory_config, odd_ctx, directory_response
    ):
        """Golden keys under a layout that shares no name with the conventional one."""
        httpx_mock.add_response(url=re.compile(r".*/users/delta.*"), json=directory_response)
        client = GraphClient(graph_config, lambda: "test-token")

        _state, count = directory.run(client, local_storage, {}, directory_config, odd_ctx)

        assert count == 2
        assert local_storage.list_files("zz-inbox") == [
            "zz-inbox/staff/bob-chen-75f99b/page.md",
            "zz-inbox/staff/jane-smith-85c5fb/page.md",
        ]
        assert local_storage.list_files("inbox") == []
        client.close()
