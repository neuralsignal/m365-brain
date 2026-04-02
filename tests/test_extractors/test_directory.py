"""Tests for directory extractor."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from pytest_httpx import HTTPXMock

from m365_extract.config import DirectoryExtractorConfig, GraphConfig
from m365_extract.extractors import directory
from m365_extract.graph_client import GraphClient
from m365_extract.markdown_writer import loads_markdown
from m365_extract.storage.local import LocalBackend

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


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
        self, httpx_mock: HTTPXMock, tmp_path, graph_config, directory_config, directory_response
    ):
        httpx_mock.add_response(
            url=re.compile(r".*/users/delta.*"),
            json=directory_response,
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        state, count = directory.run(client, storage, {}, directory_config)

        assert count == 2
        assert "delta_link" in state
        assert "last_sync" in state

        files = storage.list_files("directory")
        assert len(files) == 2

        client.close()

    def test_incremental_sync_uses_delta_link(self, httpx_mock: HTTPXMock, tmp_path, graph_config, directory_config):
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
        state, count = directory.run(client, storage, existing_state, directory_config)

        assert count == 1
        assert state["delta_link"] == "https://graph.microsoft.com/v1.0/users/delta?$deltatoken=new"
        client.close()

    def test_empty_response(self, httpx_mock: HTTPXMock, tmp_path, graph_config, directory_config):
        httpx_mock.add_response(
            url=re.compile(r".*/users/delta.*"),
            json={"value": [], "@odata.deltaLink": "https://delta?token=empty"},
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        state, count = directory.run(client, storage, {}, directory_config)
        assert count == 0
        client.close()

    def test_skips_users_without_display_name(self, httpx_mock: HTTPXMock, tmp_path, graph_config, directory_config):
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

        state, count = directory.run(client, storage, {}, directory_config)
        assert count == 0
        client.close()

    def test_skips_disabled_users_when_only_active(self, httpx_mock: HTTPXMock, tmp_path, graph_config):
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

        state, count = directory.run(client, storage, {}, config)
        assert count == 1
        client.close()

    def test_includes_disabled_users_when_not_filtering(self, httpx_mock: HTTPXMock, tmp_path, graph_config):
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

        state, count = directory.run(client, storage, {}, config)
        assert count == 2
        client.close()

    def test_user_markdown_content(
        self, httpx_mock: HTTPXMock, tmp_path, graph_config, directory_config, directory_response
    ):
        httpx_mock.add_response(
            url=re.compile(r".*/users/delta.*"),
            json=directory_response,
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        directory.run(client, storage, {}, directory_config)

        files = storage.list_files("directory")
        content = storage.read_file(files[0])
        meta, body = loads_markdown(content)

        assert meta["type"] == "directory_user"
        assert meta["source"]["service"] == "directory"
        assert meta["source"]["extractor"] == "m365-extract/directory/1.0"
        assert "# " in body
        client.close()

    def test_with_manager_and_reports(self, httpx_mock: HTTPXMock, tmp_path, graph_config):
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

        state, count = directory.run(client, storage, {}, config)
        assert count == 1

        files = storage.list_files("directory")
        content = storage.read_file(files[0])
        meta, body = loads_markdown(content)

        assert "manager" in meta
        assert "[[directory-the-boss-" in meta["manager"]
        assert len(meta["direct_reports"]) == 2
        assert "Organization" in body
        client.close()

    def test_manager_not_found_handled_gracefully(self, httpx_mock: HTTPXMock, tmp_path, graph_config):
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

        state, count = directory.run(client, storage, {}, config)
        assert count == 1

        files = storage.list_files("directory")
        content = storage.read_file(files[0])
        meta, _ = loads_markdown(content)

        assert "manager" not in meta
        client.close()
