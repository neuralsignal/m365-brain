"""Tests for SharePoint extractor."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from pytest_httpx import HTTPXMock

from m365_extract.config import GraphConfig, SharePointExtractorConfig
from m365_extract.extractors import sharepoint
from m365_extract.graph_client import GraphClient
from m365_extract.storage.local import LocalBackend

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"

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
def converters_config():
    return SAMPLE_CONVERTERS_CONFIG


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
        tmp_path,
        graph_config,
        sharepoint_config,
        converters_config,
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

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        state, count = sharepoint.run(client, storage, {}, sharepoint_config, converters_config)

        assert count == 1
        assert "last_sync" in state
        assert "delta_site-1_drive-1" in state

        files = storage.list_files("sharepoint")
        assert len(files) == 1

        content = storage.read_file(files[0])
        assert "sharepoint_file" in content
        assert "project-plan.docx" in content
        client.close()

    def test_empty_sites(self, httpx_mock: HTTPXMock, tmp_path, graph_config, sharepoint_config, converters_config):
        httpx_mock.add_response(
            url=re.compile(r".*/me/followedSites.*"),
            json={"value": []},
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        state, count = sharepoint.run(client, storage, {}, sharepoint_config, converters_config)
        assert count == 0
        client.close()

    def test_per_drive_delta_links(
        self, httpx_mock: HTTPXMock, tmp_path, graph_config, sharepoint_config, converters_config
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

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        state, count = sharepoint.run(client, storage, {}, sharepoint_config, converters_config)

        assert state["delta_s1_d1"] == "https://delta?token=d1"
        assert state["delta_s1_d2"] == "https://delta?token=d2"
        assert count == 0
        client.close()

    def test_removed_items(self, httpx_mock: HTTPXMock, tmp_path, graph_config, sharepoint_config, converters_config):
        storage = LocalBackend(str(tmp_path / "vault"))
        storage.write_file("sharepoint/site/docs/old.md", "content")

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

        existing_state = {"file_paths_s1_d1": {"rm-1": "sharepoint/site/docs/old.md"}}
        client = GraphClient(graph_config, lambda: "test-token")

        state, count = sharepoint.run(client, storage, existing_state, sharepoint_config, converters_config)

        assert count == 0
        assert "rm-1" not in state.get("file_paths_s1_d1", {})
        assert not storage.file_exists("sharepoint/site/docs/old.md")
        client.close()

    def test_frontmatter_includes_site_and_drive(
        self, httpx_mock: HTTPXMock, tmp_path, graph_config, sharepoint_config, converters_config, delta_response
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

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        sharepoint.run(client, storage, {}, sharepoint_config, converters_config)

        files = storage.list_files("sharepoint")
        content = storage.read_file(files[0])
        assert "site_name: My Site" in content
        assert "drive_name: Library" in content
        client.close()

    def test_drives_fetch_failure_skips_site(
        self, httpx_mock: HTTPXMock, tmp_path, graph_config, sharepoint_config, converters_config
    ):
        httpx_mock.add_response(
            url=re.compile(r".*/me/followedSites.*"),
            json={"value": [{"id": "s1", "displayName": "Bad Site"}]},
        )
        httpx_mock.add_response(
            url=re.compile(r".*/sites/s1/drives.*"),
            status_code=403,
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        state, count = sharepoint.run(client, storage, {}, sharepoint_config, converters_config)
        assert count == 0
        client.close()
