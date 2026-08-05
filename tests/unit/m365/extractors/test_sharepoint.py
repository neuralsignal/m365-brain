"""Tests for SharePoint extractor."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from pytest_httpx import HTTPXMock

from m365_brain.config import GraphConfig, SharePointExtractorConfig
from m365_brain.m365.client import GraphClient
from m365_brain.m365.extractors import sharepoint
from tests.unit.m365.extractors.conftest import make_ctx

FIXTURES_DIR = Path(__file__).resolve().parents[3] / "fixtures"

SAMPLE_CONVERTERS_CONFIG = {
    "backends": {"pdf": "markitdown", "docx": "markitdown", "default": "native"},
    "extraction": {"timeout_seconds": 30, "max_file_size_mb": 100, "xlsx_max_rows_per_sheet": 500},
}


@pytest.fixture()
def sharepoint_config():
    return SharePointExtractorConfig(
        enabled=True,
        poll_interval_minutes=240,
        eager_convert_patterns=[],
        convertible_extensions=[".docx", ".pdf", ".xlsx"],
        max_file_size_mb=100,
    )


@pytest.fixture()
def sharepoint_ctx(vault_paths, local_storage):
    """SharePoint genuinely converts, so it needs a real converters config — not the bare `ctx`."""
    return make_ctx(vault_paths, local_storage, SAMPLE_CONVERTERS_CONFIG)


@pytest.fixture()
def inbox(vault_paths) -> str:
    """`inbox/sharepoint` — expressed through the resolver, never spelled out."""
    return vault_paths.inbox_root("sharepoint")


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
def sites_response():
    return json.loads((FIXTURES_DIR / "sharepoint_sites_response.json").read_text())


@pytest.fixture()
def drives_response():
    return json.loads((FIXTURES_DIR / "sharepoint_drives_response.json").read_text())


@pytest.fixture()
def delta_response():
    return json.loads((FIXTURES_DIR / "sharepoint_delta_response.json").read_text())


class TestSharePointExtractor:
    def test_site_discovery_and_sync(
        self,
        httpx_mock: HTTPXMock,
        local_storage,
        graph_config,
        sharepoint_config,
        sharepoint_ctx,
        inbox,
        sites_response,
        drives_response,
        delta_response,
    ):
        # Site discovery
        httpx_mock.add_response(
            url=re.compile(r".*/me/followedSites.*"),
            json=sites_response,
        )
        # Drives for site-1
        httpx_mock.add_response(
            url=re.compile(r".*/sites/site-1/drives.*"),
            json=drives_response,
        )
        # Drives for site-2
        httpx_mock.add_response(
            url=re.compile(r".*/sites/site-2/drives.*"),
            json={"value": []},
        )
        # Delta for drive-1
        httpx_mock.add_response(
            url=re.compile(r".*/drives/drive-1/root/delta.*"),
            json=delta_response,
        )

        client = GraphClient(graph_config, lambda: "test-token")

        state, count = sharepoint.run(client, local_storage, {}, sharepoint_config, sharepoint_ctx)

        assert count == 1
        assert "last_sync" in state
        assert "delta_site-1_drive-1" in state

        files = local_storage.list_files(inbox)
        assert len(files) == 1

        content = local_storage.read_file(files[0])
        assert "sharepoint_file" in content
        assert "project-plan.docx" in content
        client.close()

    def test_empty_sites(self, httpx_mock: HTTPXMock, local_storage, graph_config, sharepoint_config, sharepoint_ctx):
        httpx_mock.add_response(
            url=re.compile(r".*/me/followedSites.*"),
            json={"value": []},
        )

        client = GraphClient(graph_config, lambda: "test-token")

        state, count = sharepoint.run(client, local_storage, {}, sharepoint_config, sharepoint_ctx)
        assert count == 0
        client.close()

    def test_per_drive_delta_links(
        self, httpx_mock: HTTPXMock, local_storage, graph_config, sharepoint_config, sharepoint_ctx
    ):
        httpx_mock.add_response(
            url=re.compile(r".*/me/followedSites.*"),
            json={"value": [{"id": "s1", "displayName": "Site A"}]},
        )
        httpx_mock.add_response(
            url=re.compile(r".*/sites/s1/drives.*"),
            json={
                "value": [
                    {"id": "d1", "name": "Docs"},
                    {"id": "d2", "name": "Archives"},
                ]
            },
        )
        httpx_mock.add_response(
            url=re.compile(r".*/drives/d1/root/delta.*"),
            json={"value": [], "@odata.deltaLink": "https://delta?token=d1"},
        )
        httpx_mock.add_response(
            url=re.compile(r".*/drives/d2/root/delta.*"),
            json={"value": [], "@odata.deltaLink": "https://delta?token=d2"},
        )

        client = GraphClient(graph_config, lambda: "test-token")

        state, count = sharepoint.run(client, local_storage, {}, sharepoint_config, sharepoint_ctx)

        assert state["delta_s1_d1"] == "https://delta?token=d1"
        assert state["delta_s1_d2"] == "https://delta?token=d2"
        assert count == 0
        client.close()

    def test_removed_items(
        self, httpx_mock: HTTPXMock, local_storage, graph_config, sharepoint_config, sharepoint_ctx, inbox
    ):
        recorded = f"{inbox}/site/docs/old.md"
        local_storage.write_file(recorded, "content")

        httpx_mock.add_response(
            url=re.compile(r".*/me/followedSites.*"),
            json={"value": [{"id": "s1", "displayName": "Site"}]},
        )
        httpx_mock.add_response(
            url=re.compile(r".*/sites/s1/drives.*"),
            json={"value": [{"id": "d1", "name": "Docs"}]},
        )
        httpx_mock.add_response(
            url=re.compile(r".*/drives/d1/root/delta.*"),
            json={
                "value": [{"id": "rm-1", "@removed": {"reason": "deleted"}}],
                "@odata.deltaLink": "https://delta?token=rm",
            },
        )

        existing_state = {"file_paths_s1_d1": {"rm-1": recorded}}
        client = GraphClient(graph_config, lambda: "test-token")

        state, count = sharepoint.run(client, local_storage, existing_state, sharepoint_config, sharepoint_ctx)

        assert count == 0
        assert "rm-1" not in state.get("file_paths_s1_d1", {})
        assert not local_storage.file_exists(recorded)
        client.close()

    def test_frontmatter_includes_site_and_drive(
        self,
        httpx_mock: HTTPXMock,
        local_storage,
        graph_config,
        sharepoint_config,
        sharepoint_ctx,
        inbox,
        delta_response,
    ):
        httpx_mock.add_response(
            url=re.compile(r".*/me/followedSites.*"),
            json={"value": [{"id": "s1", "displayName": "My Site"}]},
        )
        httpx_mock.add_response(
            url=re.compile(r".*/sites/s1/drives.*"),
            json={"value": [{"id": "d1", "name": "Library"}]},
        )
        httpx_mock.add_response(
            url=re.compile(r".*/drives/d1/root/delta.*"),
            json=delta_response,
        )

        client = GraphClient(graph_config, lambda: "test-token")

        sharepoint.run(client, local_storage, {}, sharepoint_config, sharepoint_ctx)

        files = local_storage.list_files(inbox)
        content = local_storage.read_file(files[0])
        assert "site_name: My Site" in content
        assert "drive_name: Library" in content
        client.close()

    def test_drives_fetch_failure_skips_site(
        self, httpx_mock: HTTPXMock, local_storage, graph_config, sharepoint_config, sharepoint_ctx
    ):
        httpx_mock.add_response(
            url=re.compile(r".*/me/followedSites.*"),
            json={"value": [{"id": "s1", "displayName": "Bad Site"}]},
        )
        httpx_mock.add_response(
            url=re.compile(r".*/sites/s1/drives.*"),
            status_code=403,
        )

        client = GraphClient(graph_config, lambda: "test-token")

        state, count = sharepoint.run(client, local_storage, {}, sharepoint_config, sharepoint_ctx)
        assert count == 0
        client.close()

    def test_delta_fetch_failure_returns_zero(
        self, httpx_mock: HTTPXMock, local_storage, graph_config, sharepoint_config, sharepoint_ctx
    ):
        httpx_mock.add_response(
            url=re.compile(r".*/me/followedSites.*"),
            json={"value": [{"id": "s1", "displayName": "Site"}]},
        )
        httpx_mock.add_response(
            url=re.compile(r".*/sites/s1/drives.*"),
            json={"value": [{"id": "d1", "name": "Docs"}]},
        )
        for _ in range(graph_config.max_retries + 1):
            httpx_mock.add_response(
                url=re.compile(r".*/drives/d1/root/delta.*"),
                status_code=500,
            )

        client = GraphClient(graph_config, lambda: "test-token")

        state, count = sharepoint.run(client, local_storage, {}, sharepoint_config, sharepoint_ctx)

        assert count == 0
        assert "delta_s1_d1" not in state
        client.close()

    def test_folder_items_skipped(
        self, httpx_mock: HTTPXMock, local_storage, graph_config, sharepoint_config, sharepoint_ctx, inbox
    ):
        httpx_mock.add_response(
            url=re.compile(r".*/me/followedSites.*"),
            json={"value": [{"id": "s1", "displayName": "Site"}]},
        )
        httpx_mock.add_response(
            url=re.compile(r".*/sites/s1/drives.*"),
            json={"value": [{"id": "d1", "name": "Docs"}]},
        )
        httpx_mock.add_response(
            url=re.compile(r".*/drives/d1/root/delta.*"),
            json={
                "value": [{"id": "folder-1", "name": "My Folder", "folder": {"childCount": 3}}],
                "@odata.deltaLink": "https://delta?token=f1",
            },
        )

        client = GraphClient(graph_config, lambda: "test-token")

        state, count = sharepoint.run(client, local_storage, {}, sharepoint_config, sharepoint_ctx)

        assert count == 0
        assert local_storage.list_files(inbox) == []
        client.close()

    def test_items_without_file_metadata_skipped(
        self, httpx_mock: HTTPXMock, local_storage, graph_config, sharepoint_config, sharepoint_ctx, inbox
    ):
        httpx_mock.add_response(
            url=re.compile(r".*/me/followedSites.*"),
            json={"value": [{"id": "s1", "displayName": "Site"}]},
        )
        httpx_mock.add_response(
            url=re.compile(r".*/sites/s1/drives.*"),
            json={"value": [{"id": "d1", "name": "Docs"}]},
        )
        httpx_mock.add_response(
            url=re.compile(r".*/drives/d1/root/delta.*"),
            json={
                "value": [{"id": "pkg-1", "name": "package", "package": {"type": "oneNote"}}],
                "@odata.deltaLink": "https://delta?token=p1",
            },
        )

        client = GraphClient(graph_config, lambda: "test-token")

        state, count = sharepoint.run(client, local_storage, {}, sharepoint_config, sharepoint_ctx)

        assert count == 0
        assert local_storage.list_files(inbox) == []
        client.close()

    def test_items_with_empty_name_skipped(
        self, httpx_mock: HTTPXMock, local_storage, graph_config, sharepoint_config, sharepoint_ctx, inbox
    ):
        httpx_mock.add_response(
            url=re.compile(r".*/me/followedSites.*"),
            json={"value": [{"id": "s1", "displayName": "Site"}]},
        )
        httpx_mock.add_response(
            url=re.compile(r".*/sites/s1/drives.*"),
            json={"value": [{"id": "d1", "name": "Docs"}]},
        )
        httpx_mock.add_response(
            url=re.compile(r".*/drives/d1/root/delta.*"),
            json={
                "value": [{"id": "empty-1", "name": "", "file": {"mimeType": "text/plain"}}],
                "@odata.deltaLink": "https://delta?token=e1",
            },
        )

        client = GraphClient(graph_config, lambda: "test-token")

        state, count = sharepoint.run(client, local_storage, {}, sharepoint_config, sharepoint_ctx)

        assert count == 0
        assert local_storage.list_files(inbox) == []
        client.close()


class TestNonDefaultLayout:
    """`odd_ctx` renames the SharePoint directory to `team-files` under a `zz-inbox` root.

    A golden fixture asserted against the conventional names would still pass with
    the prefix hardcoded in the extractor. This is the test that would not.
    """

    def test_golden_path_comes_from_config(
        self,
        httpx_mock: HTTPXMock,
        local_storage,
        graph_config,
        sharepoint_config,
        odd_ctx,
        sites_response,
        drives_response,
        delta_response,
    ):
        httpx_mock.add_response(url=re.compile(r".*/me/followedSites.*"), json=sites_response)
        httpx_mock.add_response(url=re.compile(r".*/sites/site-1/drives.*"), json=drives_response)
        httpx_mock.add_response(url=re.compile(r".*/sites/site-2/drives.*"), json={"value": []})
        httpx_mock.add_response(url=re.compile(r".*/drives/drive-1/root/delta.*"), json=delta_response)

        client = GraphClient(graph_config, lambda: "test-token")
        state, count = sharepoint.run(client, local_storage, {}, sharepoint_config, odd_ctx)

        assert count == 1
        assert local_storage.list_files("") == [
            "zz-inbox/team-files/engineering-hub/documents/shared/plans/project-plan-docx-206711.md",
        ]
        assert local_storage.list_files("inbox/sharepoint") == []
        assert local_storage.list_files("inbox") == []
        client.close()
