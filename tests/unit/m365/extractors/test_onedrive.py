"""Tests for OneDrive extractor."""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import patch

import pytest
from pytest_httpx import HTTPXMock

from m365_brain.config import GraphConfig, OneDriveExtractorConfig
from m365_brain.m365.client import GRAPH_BASE_URL, GraphClient
from m365_brain.m365.extractors import onedrive
from tests.unit.m365.extractors.conftest import make_ctx

FIXTURES_DIR = Path(__file__).resolve().parents[3] / "fixtures"

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
def onedrive_ctx(vault_paths, local_storage):
    """OneDrive genuinely converts, so it needs a real converters config — not the bare `ctx`."""
    return make_ctx(vault_paths, local_storage, SAMPLE_CONVERTERS_CONFIG)


@pytest.fixture()
def inbox(vault_paths) -> str:
    """`inbox/onedrive` — expressed through the resolver, never spelled out."""
    return vault_paths.inbox_root("onedrive")


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
def onedrive_response():
    return json.loads((FIXTURES_DIR / "onedrive_delta_response.json").read_text())


class TestOneDriveExtractor:
    def test_initial_sync_produces_stubs(
        self,
        httpx_mock: HTTPXMock,
        local_storage,
        graph_config,
        onedrive_config,
        onedrive_ctx,
        inbox,
        onedrive_response,
    ):
        httpx_mock.add_response(
            url=re.compile(r".*/me/drive/root/delta.*"),
            json=onedrive_response,
        )

        client = GraphClient(graph_config, lambda: "test-token")

        state, count = onedrive.run(client, local_storage, {}, onedrive_config, onedrive_ctx)

        # 2 files (folder is skipped)
        assert count == 2
        assert "delta_link" in state
        assert "file_paths" in state
        assert len(state["file_paths"]) == 2
        assert "last_sync" in state

        files = local_storage.list_files(inbox)
        assert len(files) == 2
        client.close()

    def test_incremental_sync_uses_delta_link(
        self, httpx_mock: HTTPXMock, local_storage, graph_config, onedrive_config, onedrive_ctx
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

        client = GraphClient(graph_config, lambda: "test-token")

        existing_state = {"delta_link": delta_url}
        state, count = onedrive.run(client, local_storage, existing_state, onedrive_config, onedrive_ctx)

        assert count == 1
        assert state["delta_link"] == f"{GRAPH_BASE_URL}/delta?token=new"
        client.close()

    def test_folders_are_skipped(
        self, httpx_mock: HTTPXMock, local_storage, graph_config, onedrive_config, onedrive_ctx
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

        client = GraphClient(graph_config, lambda: "test-token")

        state, count = onedrive.run(client, local_storage, {}, onedrive_config, onedrive_ctx)
        assert count == 0
        client.close()

    def test_removed_items_are_deleted(
        self, httpx_mock: HTTPXMock, local_storage, graph_config, onedrive_config, onedrive_ctx, inbox
    ):
        # Pre-populate a file
        recorded = f"{inbox}/old-file.md"
        local_storage.write_file(recorded, "old content")

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
        existing_state = {"file_paths": {"file-to-remove": recorded}}

        state, count = onedrive.run(client, local_storage, existing_state, onedrive_config, onedrive_ctx)

        assert count == 0
        assert "file-to-remove" not in state["file_paths"]
        assert not local_storage.file_exists(recorded)
        client.close()

    def test_eager_convert_pattern(self, httpx_mock: HTTPXMock, local_storage, graph_config, onedrive_ctx, inbox):
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

        client = GraphClient(graph_config, lambda: "test-token")

        with patch(
            "m365_brain.m365.extractors._file_helpers.convert_document",
            return_value="# Converted Document",
        ):
            state, count = onedrive.run(client, local_storage, {}, config, onedrive_ctx)

        assert count == 1
        files = local_storage.list_files(inbox)
        assert len(files) == 1
        content = local_storage.read_file(files[0])
        assert "Converted Document" in content
        client.close()

    def test_non_convertible_gets_stub(
        self, httpx_mock: HTTPXMock, local_storage, graph_config, onedrive_config, onedrive_ctx, inbox
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

        client = GraphClient(graph_config, lambda: "test-token")

        state, count = onedrive.run(client, local_storage, {}, onedrive_config, onedrive_ctx)
        assert count == 1

        files = local_storage.list_files(inbox)
        content = local_storage.read_file(files[0])
        assert "not_convertible" in content
        client.close()

    def test_run_skips_item_without_file_key(
        self, httpx_mock: HTTPXMock, local_storage, graph_config, onedrive_config, onedrive_ctx, inbox
    ):
        httpx_mock.add_response(
            url=re.compile(r".*/me/drive/root/delta.*"),
            json={
                "value": [
                    {
                        "id": "onenote-notebook",
                        "name": "My Notebook",
                        "size": 0,
                        "parentReference": {"path": "/drive/root:"},
                        "lastModifiedDateTime": "2026-03-12T10:00:00Z",
                        "lastModifiedBy": {"user": {"displayName": "Test"}},
                        "webUrl": "",
                    }
                ],
                "@odata.deltaLink": "https://delta?token=nofile",
            },
        )

        client = GraphClient(graph_config, lambda: "test-token")

        state, count = onedrive.run(client, local_storage, {}, onedrive_config, onedrive_ctx)
        assert count == 0
        assert local_storage.list_files(inbox) == []
        client.close()

    def test_run_skips_item_with_empty_filename(
        self, httpx_mock: HTTPXMock, local_storage, graph_config, onedrive_config, onedrive_ctx, inbox
    ):
        httpx_mock.add_response(
            url=re.compile(r".*/me/drive/root/delta.*"),
            json={
                "value": [
                    {
                        "id": "empty-name-file",
                        "name": "",
                        "size": 100,
                        "file": {"mimeType": "application/octet-stream"},
                        "parentReference": {"path": "/drive/root:"},
                        "lastModifiedDateTime": "2026-03-12T10:00:00Z",
                        "lastModifiedBy": {"user": {"displayName": "Test"}},
                        "webUrl": "",
                    }
                ],
                "@odata.deltaLink": "https://delta?token=emptyname",
            },
        )

        client = GraphClient(graph_config, lambda: "test-token")

        state, count = onedrive.run(client, local_storage, {}, onedrive_config, onedrive_ctx)
        assert count == 0
        assert local_storage.list_files(inbox) == []
        client.close()

    def test_empty_delta(self, httpx_mock: HTTPXMock, local_storage, graph_config, onedrive_config, onedrive_ctx):
        httpx_mock.add_response(
            url=re.compile(r".*/me/drive/root/delta.*"),
            json={"value": [], "@odata.deltaLink": "https://delta?token=empty"},
        )

        client = GraphClient(graph_config, lambda: "test-token")

        state, count = onedrive.run(client, local_storage, {}, onedrive_config, onedrive_ctx)
        assert count == 0
        assert state["delta_link"] == "https://delta?token=empty"
        client.close()

    def test_stub_contains_frontmatter(
        self, httpx_mock: HTTPXMock, local_storage, graph_config, onedrive_config, onedrive_ctx, inbox
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

        client = GraphClient(graph_config, lambda: "test-token")

        onedrive.run(client, local_storage, {}, onedrive_config, onedrive_ctx)

        files = local_storage.list_files(inbox)
        content = local_storage.read_file(files[0])
        assert "type: onedrive_file" in content
        assert "notes.docx" in content
        client.close()


class TestRemovalRoundTrip:
    """Write, remove, then remove again — the third cycle must be a clean no-op.

    Upstream re-sends a `@removed` marker for an id it has already reported, so
    the second delete has to find nothing and say nothing rather than 404.
    """

    def test_write_then_remove_then_repeat(
        self, httpx_mock: HTTPXMock, local_storage, graph_config, onedrive_config, onedrive_ctx, inbox
    ):
        cycle_1 = f"{GRAPH_BASE_URL}/me/drive/root/delta?$deltatoken=cycle-1"
        cycle_2 = f"{GRAPH_BASE_URL}/me/drive/root/delta?$deltatoken=cycle-2"
        removed_item = {"id": "rt-1", "@removed": {"reason": "deleted"}}

        httpx_mock.add_response(
            url=re.compile(r".*/me/drive/root/delta\?%24select.*"),
            json={
                "value": [
                    {
                        "id": "rt-1",
                        "name": "round-trip.txt",
                        "size": 42,
                        "file": {"mimeType": "text/plain"},
                        "parentReference": {"path": "/drive/root:"},
                        "lastModifiedDateTime": "2026-03-12T10:00:00Z",
                        "lastModifiedBy": {"user": {"displayName": "Test"}},
                        "webUrl": "",
                    }
                ],
                "@odata.deltaLink": cycle_1,
            },
        )
        httpx_mock.add_response(url=cycle_1, json={"value": [removed_item], "@odata.deltaLink": cycle_2})
        httpx_mock.add_response(url=cycle_2, json={"value": [removed_item], "@odata.deltaLink": cycle_2})

        client = GraphClient(graph_config, lambda: "test-token")

        state, count = onedrive.run(client, local_storage, {}, onedrive_config, onedrive_ctx)
        assert count == 1
        written_path = state["file_paths"]["rt-1"]
        assert local_storage.file_exists(written_path)

        state, count = onedrive.run(client, local_storage, state, onedrive_config, onedrive_ctx)
        assert count == 0
        assert "rt-1" not in state["file_paths"]
        assert not local_storage.file_exists(written_path)
        assert local_storage.list_files(inbox) == []

        state, count = onedrive.run(client, local_storage, state, onedrive_config, onedrive_ctx)
        assert count == 0
        assert "rt-1" not in state["file_paths"]
        assert local_storage.list_files(inbox) == []
        client.close()


class TestNonDefaultLayout:
    """`odd_ctx` renames the OneDrive directory to `my-files` under a `zz-inbox` root.

    A golden fixture asserted against the conventional names would still pass with
    the prefix hardcoded in the extractor. This is the test that would not.
    """

    def test_golden_paths_come_from_config(
        self, httpx_mock: HTTPXMock, local_storage, graph_config, onedrive_config, odd_ctx, onedrive_response
    ):
        httpx_mock.add_response(url=re.compile(r".*/me/drive/root/delta.*"), json=onedrive_response)

        client = GraphClient(graph_config, lambda: "test-token")
        state, count = onedrive.run(client, local_storage, {}, onedrive_config, odd_ctx)

        assert count == 2
        assert local_storage.list_files("") == [
            "zz-inbox/my-files/documents/reports/quarterly-report-docx-c2bb3c.md",
            "zz-inbox/my-files/finance/budget-xlsx-69453e.md",
        ]
        assert local_storage.list_files("inbox/onedrive") == []
        assert local_storage.list_files("inbox") == []
        client.close()
