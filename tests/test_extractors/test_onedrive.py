"""Tests for OneDrive extractor."""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import patch

import pytest
from pytest_httpx import HTTPXMock

from m365_extract.config import GraphConfig, OneDriveExtractorConfig
from m365_extract.extractors import onedrive
from m365_extract.graph_client import GRAPH_BASE_URL, GraphClient
from m365_extract.storage.local import LocalBackend

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"

SAMPLE_CONVERTERS_CONFIG = {
    "backends": {"pdf": "markitdown", "docx": "markitdown", "default": "native"},
    "extraction": {"timeout_seconds": 30, "max_file_size_mb": 100, "xlsx_max_rows_per_sheet": 500},
}


@pytest.fixture()
def onedrive_config():
    return OneDriveExtractorConfig(
        enabled=True,
        poll_interval_minutes=120,
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
    )


@pytest.fixture()
def onedrive_response():
    return json.loads((FIXTURES_DIR / "onedrive_delta_response.json").read_text())


class TestOneDriveExtractor:
    def test_initial_sync_produces_stubs(
        self, httpx_mock: HTTPXMock, tmp_path, graph_config, onedrive_config, converters_config, onedrive_response
    ):
        httpx_mock.add_response(
            url=re.compile(r".*/me/drive/root/delta.*"),
            json=onedrive_response,
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        state, count = onedrive.run(client, storage, {}, onedrive_config, converters_config)

        # 2 files (folder is skipped)
        assert count == 2
        assert "delta_link" in state
        assert "file_paths" in state
        assert len(state["file_paths"]) == 2
        assert "last_sync" in state

        files = storage.list_files("onedrive")
        assert len(files) == 2
        client.close()

    def test_incremental_sync_uses_delta_link(
        self, httpx_mock: HTTPXMock, tmp_path, graph_config, onedrive_config, converters_config
    ):
        delta_url = f"{GRAPH_BASE_URL}/me/drive/root/delta?$deltatoken=existing"
        httpx_mock.add_response(
            url=delta_url,
            json={
                "value": [
                    {
                        "id": "new-file",
                        "name": "new-doc.txt",
                        "size": 100,
                        "file": {"mimeType": "text/plain"},
                        "parentReference": {"path": "/drive/root:"},
                        "lastModifiedDateTime": "2026-03-12T15:00:00Z",
                        "lastModifiedBy": {"user": {"displayName": "Test"}},
                        "webUrl": "",
                    }
                ],
                "@odata.deltaLink": f"{GRAPH_BASE_URL}/delta?token=new",
            },
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        existing_state = {"delta_link": delta_url}
        state, count = onedrive.run(client, storage, existing_state, onedrive_config, converters_config)

        assert count == 1
        assert state["delta_link"] == f"{GRAPH_BASE_URL}/delta?token=new"
        client.close()

    def test_folders_are_skipped(
        self, httpx_mock: HTTPXMock, tmp_path, graph_config, onedrive_config, converters_config
    ):
        httpx_mock.add_response(
            url=re.compile(r".*/me/drive/root/delta.*"),
            json={
                "value": [
                    {"id": "folder-1", "name": "MyFolder", "folder": {"childCount": 3}},
                ],
                "@odata.deltaLink": "https://delta?token=folders",
            },
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        state, count = onedrive.run(client, storage, {}, onedrive_config, converters_config)
        assert count == 0
        client.close()

    def test_removed_items_are_deleted(
        self, httpx_mock: HTTPXMock, tmp_path, graph_config, onedrive_config, converters_config
    ):
        storage = LocalBackend(str(tmp_path / "vault"))
        # Pre-populate a file
        storage.write_file("onedrive/old-file.md", "old content")

        httpx_mock.add_response(
            url=re.compile(r".*/me/drive/root/delta.*"),
            json={
                "value": [
                    {"id": "file-to-remove", "@removed": {"reason": "deleted"}},
                ],
                "@odata.deltaLink": "https://delta?token=removed",
            },
        )

        client = GraphClient(graph_config, lambda: "test-token")
        existing_state = {"file_paths": {"file-to-remove": "onedrive/old-file.md"}}

        state, count = onedrive.run(client, storage, existing_state, onedrive_config, converters_config)

        assert count == 0
        assert "file-to-remove" not in state["file_paths"]
        assert not storage.file_exists("onedrive/old-file.md")
        client.close()

    def test_eager_convert_pattern(self, httpx_mock: HTTPXMock, tmp_path, graph_config, converters_config):
        config = OneDriveExtractorConfig(
            enabled=True,
            poll_interval_minutes=120,
            eager_convert_patterns=["*.docx"],
            convertible_extensions=[".docx"],
            max_file_size_mb=100,
        )

        httpx_mock.add_response(
            url=re.compile(r".*/me/drive/root/delta.*"),
            json={
                "value": [
                    {
                        "id": "eager-file",
                        "name": "important.docx",
                        "size": 5000,
                        "file": {"mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
                        "parentReference": {"path": "/drive/root:/Documents"},
                        "lastModifiedDateTime": "2026-03-12T10:00:00Z",
                        "lastModifiedBy": {"user": {"displayName": "Alice"}},
                        "webUrl": "https://example.com/important.docx",
                        "@microsoft.graph.downloadUrl": "https://tenant.sharepoint.com/sites/docs/eager",
                    }
                ],
                "@odata.deltaLink": "https://delta?token=eager",
            },
        )

        # Mock the download
        httpx_mock.add_response(
            url="https://tenant.sharepoint.com/sites/docs/eager",
            content=b"fake docx bytes",
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        with patch(
            "m365_extract.extractors._file_helpers.convert_document",
            return_value="# Converted Document",
        ):
            state, count = onedrive.run(client, storage, {}, config, converters_config)

        assert count == 1
        files = storage.list_files("onedrive")
        assert len(files) == 1
        content = storage.read_file(files[0])
        assert "Converted Document" in content
        client.close()

    def test_non_convertible_gets_stub(
        self, httpx_mock: HTTPXMock, tmp_path, graph_config, onedrive_config, converters_config
    ):
        httpx_mock.add_response(
            url=re.compile(r".*/me/drive/root/delta.*"),
            json={
                "value": [
                    {
                        "id": "img-file",
                        "name": "photo.png",
                        "size": 2000,
                        "file": {"mimeType": "image/png"},
                        "parentReference": {"path": "/drive/root:/Photos"},
                        "lastModifiedDateTime": "2026-03-12T10:00:00Z",
                        "lastModifiedBy": {"user": {"displayName": "Bob"}},
                        "webUrl": "",
                    }
                ],
                "@odata.deltaLink": "https://delta?token=img",
            },
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        state, count = onedrive.run(client, storage, {}, onedrive_config, converters_config)
        assert count == 1

        files = storage.list_files("onedrive")
        content = storage.read_file(files[0])
        assert "not_convertible" in content
        client.close()

    def test_empty_delta(self, httpx_mock: HTTPXMock, tmp_path, graph_config, onedrive_config, converters_config):
        httpx_mock.add_response(
            url=re.compile(r".*/me/drive/root/delta.*"),
            json={"value": [], "@odata.deltaLink": "https://delta?token=empty"},
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        state, count = onedrive.run(client, storage, {}, onedrive_config, converters_config)
        assert count == 0
        assert state["delta_link"] == "https://delta?token=empty"
        client.close()

    def test_stub_contains_frontmatter(
        self, httpx_mock: HTTPXMock, tmp_path, graph_config, onedrive_config, converters_config
    ):
        httpx_mock.add_response(
            url=re.compile(r".*/me/drive/root/delta.*"),
            json={
                "value": [
                    {
                        "id": "fm-file",
                        "name": "notes.docx",
                        "size": 3000,
                        "file": {"mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"},
                        "parentReference": {"path": "/drive/root:/Notes"},
                        "lastModifiedDateTime": "2026-03-12T11:00:00Z",
                        "lastModifiedBy": {"user": {"displayName": "Test User"}},
                        "webUrl": "https://example.com/notes.docx",
                    }
                ],
                "@odata.deltaLink": "https://delta?token=fm",
            },
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        onedrive.run(client, storage, {}, onedrive_config, converters_config)

        files = storage.list_files("onedrive")
        content = storage.read_file(files[0])
        assert "type: onedrive_file" in content
        assert "notes.docx" in content
        client.close()
