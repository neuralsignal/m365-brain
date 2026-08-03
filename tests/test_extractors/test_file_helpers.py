"""Tests for shared file processing helpers."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from hypothesis import given
from hypothesis import strategies as st

from m365_extract.extractors._file_helpers import (
    FileProcessingConfig,
    FileProcessingContext,
    build_storage_path,
    extract_parent_path,
    handle_removed_item,
    process_drive_item,
    should_eager_convert,
)
from m365_extract.graph_client import GraphApiError
from m365_extract.storage.local import LocalBackend

SAMPLE_CONVERTERS_CONFIG = {
    "backends": {"pdf": "markitdown", "docx": "markitdown", "default": "native"},
    "extraction": {"timeout_seconds": 30, "max_file_size_mb": 100, "xlsx_max_rows_per_sheet": 500},
}


class TestExtractParentPath:
    def test_strips_drive_root_prefix(self):
        ref = {"path": "/drive/root:/Documents/Reports"}
        assert extract_parent_path(ref) == "Documents/Reports"

    def test_strips_drives_id_prefix(self):
        ref = {"path": "/drives/abc123/root:/Shared/Files"}
        assert extract_parent_path(ref) == "Shared/Files"

    def test_empty_path(self):
        ref = {"path": ""}
        assert extract_parent_path(ref) == ""

    def test_no_path_key(self):
        ref = {}
        assert extract_parent_path(ref) == ""

    def test_root_path_only(self):
        ref = {"path": "/drive/root:"}
        assert extract_parent_path(ref) == ""


class TestBuildStoragePath:
    def test_basic_path(self):
        path = build_storage_path("onedrive", "Documents/Reports", "report.docx", "item-123")
        assert path.startswith("onedrive/")
        assert "documents" in path
        assert "reports" in path
        assert "report-docx" in path
        assert path.endswith(".md")

    def test_empty_parent_path(self):
        path = build_storage_path("onedrive", "", "file.txt", "item-456")
        assert path.startswith("onedrive/")
        assert "file-txt" in path

    def test_deterministic(self):
        path1 = build_storage_path("onedrive", "Docs", "file.pdf", "id-1")
        path2 = build_storage_path("onedrive", "Docs", "file.pdf", "id-1")
        assert path1 == path2

    def test_different_ids_different_paths(self):
        path1 = build_storage_path("onedrive", "Docs", "file.pdf", "id-1")
        path2 = build_storage_path("onedrive", "Docs", "file.pdf", "id-2")
        assert path1 != path2

    @given(st.text(min_size=1, max_size=50, alphabet=st.characters(whitelist_categories=("L", "N"))))
    def test_always_produces_valid_path(self, name):
        path = build_storage_path("prefix", "parent", name, "id")
        assert path.startswith("prefix/")
        assert path.endswith(".md")
        assert "\x00" not in path


class TestExtractParentPathProperty:
    @given(
        st.text(min_size=1, max_size=30, alphabet=st.characters(whitelist_categories=("L", "N", "P"))).filter(
            lambda s: ":" not in s
        ),
        st.text(min_size=1, max_size=30, alphabet=st.characters(whitelist_categories=("L", "N", "P"))).filter(
            lambda s: ":" not in s
        ),
    )
    def test_strips_prefix_up_to_colon(self, prefix, suffix):
        ref = {"path": f"{prefix}:{suffix}"}
        result = extract_parent_path(ref)
        # Result must not contain the prefix or colon
        assert ":" not in result
        # Result is the suffix with leading slashes stripped
        assert result == suffix.lstrip("/")


class TestBuildStoragePathProperty:
    @given(
        prefix=st.text(min_size=1, max_size=20, alphabet=st.characters(whitelist_categories=("L", "N"))),
        file_name=st.text(min_size=1, max_size=20, alphabet=st.characters(whitelist_categories=("L", "N"))),
        item_id=st.text(min_size=1, max_size=20, alphabet=st.characters(whitelist_categories=("L", "N"))),
    )
    def test_output_starts_with_prefix_ends_with_md_contains_hash(self, prefix, file_name, item_id):
        from m365_extract.markdown_writer import short_hash

        path = build_storage_path(prefix, "parent", file_name, item_id)
        assert path.startswith(f"{prefix}/")
        assert path.endswith(".md")
        hsh = short_hash(item_id, 6)
        assert hsh in path


class TestShouldEagerConvert:
    def test_matches_glob_pattern(self):
        assert should_eager_convert("report.docx", ["*.docx"])

    def test_no_match(self):
        assert not should_eager_convert("image.png", ["*.docx", "*.pdf"])

    def test_case_insensitive(self):
        assert should_eager_convert("Report.DOCX", ["*.docx"])

    def test_prefix_pattern(self):
        assert should_eager_convert("report-q1.pdf", ["report-*.pdf"])

    def test_empty_patterns(self):
        assert not should_eager_convert("anything.docx", [])

    def test_star_matches_all(self):
        assert should_eager_convert("anything.xyz", ["*"])


class TestHandleRemovedItem:
    def test_deletes_tracked_file(self, tmp_path):
        storage = LocalBackend(str(tmp_path / "vault"))
        storage.write_file("onedrive/test.md", "content")

        file_paths = {"item-1": "onedrive/test.md"}
        handle_removed_item(storage, "item-1", file_paths)

        assert "item-1" not in file_paths
        assert not storage.file_exists("onedrive/test.md")

    def test_ignores_untracked_item(self, tmp_path):
        storage = LocalBackend(str(tmp_path / "vault"))
        file_paths = {}
        handle_removed_item(storage, "unknown-id", file_paths)
        assert len(file_paths) == 0


class TestProcessDriveItem:
    def test_stub_for_non_eager_convertible(self, tmp_path):
        storage = LocalBackend(str(tmp_path / "vault"))
        item = {
            "name": "doc.docx",
            "size": 1024,
            "lastModifiedDateTime": "2026-03-12T10:00:00Z",
        }
        fm = {"title": "doc.docx", "conversion_status": "pending"}

        result = process_drive_item(
            ctx=FileProcessingContext(
                client=MagicMock(),
                storage=storage,
                file_config=FileProcessingConfig(
                    eager_patterns=[],
                    convertible_extensions=[".docx"],
                    max_file_size_mb=100,
                    converters_config=SAMPLE_CONVERTERS_CONFIG,
                ),
            ),
            item=item,
            storage_path="onedrive/doc.md",
            frontmatter=fm,
        )

        assert result is True
        content = storage.read_file("onedrive/doc.md")
        assert "pending" in content

    def test_stub_for_non_convertible(self, tmp_path):
        storage = LocalBackend(str(tmp_path / "vault"))
        item = {
            "name": "image.png",
            "size": 5000,
            "lastModifiedDateTime": "2026-03-12T10:00:00Z",
        }
        fm = {"title": "image.png", "conversion_status": "pending"}

        result = process_drive_item(
            ctx=FileProcessingContext(
                client=MagicMock(),
                storage=storage,
                file_config=FileProcessingConfig(
                    eager_patterns=["*.png"],
                    convertible_extensions=[".docx"],
                    max_file_size_mb=100,
                    converters_config=SAMPLE_CONVERTERS_CONFIG,
                ),
            ),
            item=item,
            storage_path="onedrive/image.md",
            frontmatter=fm,
        )

        assert result is True
        content = storage.read_file("onedrive/image.md")
        assert "not_convertible" in content

    def test_eager_convert_downloads_and_converts(self, tmp_path):
        storage = LocalBackend(str(tmp_path / "vault"))
        mock_client = MagicMock()
        mock_client.get_bytes.return_value = b"fake docx content"

        item = {
            "name": "report.docx",
            "size": 1024,
            "lastModifiedDateTime": "2026-03-12T10:00:00Z",
            "@microsoft.graph.downloadUrl": "https://download.example.com/file",
        }
        fm = {"title": "report.docx", "conversion_status": "pending"}

        with patch(
            "m365_extract.extractors._file_helpers.convert_document",
            return_value="# Report Content",
        ):
            result = process_drive_item(
                ctx=FileProcessingContext(
                    client=mock_client,
                    storage=storage,
                    file_config=FileProcessingConfig(
                        eager_patterns=["*.docx"],
                        convertible_extensions=[".docx"],
                        max_file_size_mb=100,
                        converters_config=SAMPLE_CONVERTERS_CONFIG,
                    ),
                ),
                item=item,
                storage_path="onedrive/report.md",
                frontmatter=fm,
            )

        assert result is True
        content = storage.read_file("onedrive/report.md")
        assert "Report Content" in content
        assert fm["conversion_status"] == "converted"

    def test_eager_convert_handles_missing_download_url(self, tmp_path):
        storage = LocalBackend(str(tmp_path / "vault"))
        item = {
            "name": "report.docx",
            "size": 1024,
            "lastModifiedDateTime": "2026-03-12T10:00:00Z",
            # No @microsoft.graph.downloadUrl
        }
        fm = {"title": "report.docx", "conversion_status": "pending"}

        result = process_drive_item(
            ctx=FileProcessingContext(
                client=MagicMock(),
                storage=storage,
                file_config=FileProcessingConfig(
                    eager_patterns=["*.docx"],
                    convertible_extensions=[".docx"],
                    max_file_size_mb=100,
                    converters_config=SAMPLE_CONVERTERS_CONFIG,
                ),
            ),
            item=item,
            storage_path="onedrive/report.md",
            frontmatter=fm,
        )

        assert result is True
        assert fm["conversion_status"] == "error_no_download_url"

    def test_rejects_oversized_file_before_download(self, tmp_path):
        storage = LocalBackend(str(tmp_path / "vault"))
        mock_client = MagicMock()

        item = {
            "name": "huge.docx",
            "size": 200 * 1024 * 1024,  # 200 MB
            "lastModifiedDateTime": "2026-03-12T10:00:00Z",
            "@microsoft.graph.downloadUrl": "https://download.example.com/huge",
        }
        fm = {"title": "huge.docx", "conversion_status": "pending"}

        result = process_drive_item(
            ctx=FileProcessingContext(
                client=mock_client,
                storage=storage,
                file_config=FileProcessingConfig(
                    eager_patterns=["*.docx"],
                    convertible_extensions=[".docx"],
                    max_file_size_mb=100,
                    converters_config=SAMPLE_CONVERTERS_CONFIG,
                ),
            ),
            item=item,
            storage_path="onedrive/huge.md",
            frontmatter=fm,
        )

        assert result is True
        assert fm["conversion_status"] == "error_too_large"
        # Must NOT have tried to download
        mock_client.get_bytes.assert_not_called()
        content = storage.read_file("onedrive/huge.md")
        assert "exceeding limit" in content

    def test_download_failure_writes_error_stub(self, tmp_path):
        storage = LocalBackend(str(tmp_path / "vault"))
        mock_client = MagicMock()
        mock_client.get_bytes.side_effect = GraphApiError("500 Server Error", 500)

        item = {
            "name": "report.docx",
            "size": 1024,
            "lastModifiedDateTime": "2026-03-12T10:00:00Z",
            "@microsoft.graph.downloadUrl": "https://download.example.com/file",
        }
        fm = {"title": "report.docx", "conversion_status": "pending"}

        result = process_drive_item(
            ctx=FileProcessingContext(
                client=mock_client,
                storage=storage,
                file_config=FileProcessingConfig(
                    eager_patterns=["*.docx"],
                    convertible_extensions=[".docx"],
                    max_file_size_mb=100,
                    converters_config=SAMPLE_CONVERTERS_CONFIG,
                ),
            ),
            item=item,
            storage_path="onedrive/report.md",
            frontmatter=fm,
        )

        assert result is True
        assert fm["conversion_status"] == "error_download"


class TestProcessDriveItemFallbackFetch:
    def test_fetches_download_url_when_missing_from_delta(self, tmp_path):
        """When item lacks downloadUrl, fetches it individually via client.get()."""
        storage = LocalBackend(str(tmp_path / "vault"))
        mock_client = MagicMock()
        mock_client.get.return_value = {
            "@microsoft.graph.downloadUrl": "https://download.example.com/fetched",
        }
        mock_client.get_bytes.return_value = b"fake docx content"

        item = {
            "name": "report.docx",
            "id": "item-abc",
            "size": 1024,
            "lastModifiedDateTime": "2026-03-12T10:00:00Z",
            # No @microsoft.graph.downloadUrl — triggers fallback fetch
        }
        fm = {"title": "report.docx", "conversion_status": "pending"}

        with patch(
            "m365_extract.extractors._file_helpers.convert_document",
            return_value="# Converted",
        ):
            result = process_drive_item(
                ctx=FileProcessingContext(
                    client=mock_client,
                    storage=storage,
                    file_config=FileProcessingConfig(
                        eager_patterns=["*.docx"],
                        convertible_extensions=[".docx"],
                        max_file_size_mb=100,
                        converters_config=SAMPLE_CONVERTERS_CONFIG,
                    ),
                ),
                item=item,
                storage_path="onedrive/report.md",
                frontmatter=fm,
            )

        assert result is True
        assert fm["conversion_status"] == "converted"
        mock_client.get.assert_called_once_with(
            "/me/drive/items/item-abc",
            params={"$select": "@microsoft.graph.downloadUrl"},
        )
        mock_client.get_bytes.assert_called_once_with("https://download.example.com/fetched")
        content = storage.read_file("onedrive/report.md")
        assert "Converted" in content

    def test_handles_graph_api_error_during_individual_fetch(self, tmp_path):
        """When individual item fetch raises GraphApiError, logs warning and writes no-url stub."""
        storage = LocalBackend(str(tmp_path / "vault"))
        mock_client = MagicMock()
        mock_client.get.side_effect = GraphApiError("404 Not Found", 404)

        item = {
            "name": "report.docx",
            "id": "item-abc",
            "size": 1024,
            "lastModifiedDateTime": "2026-03-12T10:00:00Z",
        }
        fm = {"title": "report.docx", "conversion_status": "pending"}

        result = process_drive_item(
            ctx=FileProcessingContext(
                client=mock_client,
                storage=storage,
                file_config=FileProcessingConfig(
                    eager_patterns=["*.docx"],
                    convertible_extensions=[".docx"],
                    max_file_size_mb=100,
                    converters_config=SAMPLE_CONVERTERS_CONFIG,
                ),
            ),
            item=item,
            storage_path="onedrive/report.md",
            frontmatter=fm,
        )

        assert result is True
        assert fm["conversion_status"] == "error_no_download_url"
        content = storage.read_file("onedrive/report.md")
        assert "No download URL available" in content


class TestProcessDriveItemConversionError:
    def test_writes_error_stub_on_conversion_failure(self, tmp_path):
        """When convert_document raises ValueError, writes error_conversion stub."""
        storage = LocalBackend(str(tmp_path / "vault"))
        mock_client = MagicMock()
        mock_client.get_bytes.return_value = b"fake docx content"

        item = {
            "name": "report.docx",
            "size": 1024,
            "lastModifiedDateTime": "2026-03-12T10:00:00Z",
            "@microsoft.graph.downloadUrl": "https://download.example.com/file",
        }
        fm = {"title": "report.docx", "conversion_status": "pending"}

        with patch(
            "m365_extract.extractors._file_helpers.convert_document",
            side_effect=ValueError("unsupported format"),
        ):
            result = process_drive_item(
                ctx=FileProcessingContext(
                    client=mock_client,
                    storage=storage,
                    file_config=FileProcessingConfig(
                        eager_patterns=["*.docx"],
                        convertible_extensions=[".docx"],
                        max_file_size_mb=100,
                        converters_config=SAMPLE_CONVERTERS_CONFIG,
                    ),
                ),
                item=item,
                storage_path="onedrive/report.md",
                frontmatter=fm,
            )

        assert result is True
        assert fm["conversion_status"] == "error_conversion"
        content = storage.read_file("onedrive/report.md")
        assert "Conversion failed" in content
        assert "unsupported format" in content


class TestGraphApiErrorNotSwallowed:
    def test_download_graph_api_error_produces_error_stub(self, tmp_path):
        """GraphApiError from get_bytes is caught and recorded, not silently swallowed."""
        storage = LocalBackend(str(tmp_path / "vault"))
        mock_client = MagicMock()
        mock_client.get_bytes.side_effect = GraphApiError("403 Forbidden: insufficient privileges", 403)

        item = {
            "name": "secret.docx",
            "size": 1024,
            "lastModifiedDateTime": "2026-03-12T10:00:00Z",
            "@microsoft.graph.downloadUrl": "https://download.example.com/secret",
        }
        fm = {"title": "secret.docx", "conversion_status": "pending"}

        result = process_drive_item(
            ctx=FileProcessingContext(
                client=mock_client,
                storage=storage,
                file_config=FileProcessingConfig(
                    eager_patterns=["*.docx"],
                    convertible_extensions=[".docx"],
                    max_file_size_mb=100,
                    converters_config=SAMPLE_CONVERTERS_CONFIG,
                ),
            ),
            item=item,
            storage_path="onedrive/secret.md",
            frontmatter=fm,
        )

        assert result is True
        assert fm["conversion_status"] == "error_download"
        content = storage.read_file("onedrive/secret.md")
        assert "Download failed" in content
        assert "403 Forbidden" in content

    def test_refetch_graph_api_error_produces_no_url_stub(self, tmp_path):
        """GraphApiError from client.get() during re-fetch is caught, not silently swallowed."""
        storage = LocalBackend(str(tmp_path / "vault"))
        mock_client = MagicMock()
        mock_client.get.side_effect = GraphApiError("429 Too Many Requests", 429)

        item = {
            "name": "report.docx",
            "id": "item-xyz",
            "size": 1024,
            "lastModifiedDateTime": "2026-03-12T10:00:00Z",
        }
        fm = {"title": "report.docx", "conversion_status": "pending"}

        result = process_drive_item(
            ctx=FileProcessingContext(
                client=mock_client,
                storage=storage,
                file_config=FileProcessingConfig(
                    eager_patterns=["*.docx"],
                    convertible_extensions=[".docx"],
                    max_file_size_mb=100,
                    converters_config=SAMPLE_CONVERTERS_CONFIG,
                ),
            ),
            item=item,
            storage_path="onedrive/report.md",
            frontmatter=fm,
        )

        assert result is True
        assert fm["conversion_status"] == "error_no_download_url"
        content = storage.read_file("onedrive/report.md")
        assert "No download URL available" in content
