"""Tests for document converter."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from m365_brain.converters.document import DocumentConversionError, convert_document

SAMPLE_CONVERTERS_CONFIG = {
    "backends": {"pdf": "markitdown", "docx": "markitdown", "default": "native"},
    "extraction": {"timeout_seconds": 30, "max_file_size_mb": 100, "xlsx_max_rows_per_sheet": 500},
}


class TestConvertDocument:
    def test_calls_obsidian_import(self, tmp_path):
        test_file = tmp_path / "test.docx"
        test_file.write_bytes(b"fake docx content")

        mock_config = MagicMock()
        with (
            patch("obsidian_import.config_from_overrides", return_value=mock_config) as mock_overrides,
            patch("obsidian_import.extract_text", return_value="# Converted") as mock_extract,
        ):
            result = convert_document(
                file_path=test_file,
                converters_config=SAMPLE_CONVERTERS_CONFIG,
            )

        assert result == "# Converted"
        mock_overrides.assert_called_once_with(SAMPLE_CONVERTERS_CONFIG)
        mock_extract.assert_called_once_with(test_file, mock_config)

    def test_import_error_propagates(self, tmp_path):
        test_file = tmp_path / "test.pdf"
        test_file.write_bytes(b"fake pdf")

        with patch.dict("sys.modules", {"obsidian_import": None}), pytest.raises((ImportError, TypeError)):
            convert_document(
                file_path=test_file,
                converters_config=SAMPLE_CONVERTERS_CONFIG,
            )

    def test_overrides_passed_verbatim(self, tmp_path):
        test_file = tmp_path / "test.txt"
        test_file.write_bytes(b"hello")

        overrides = {"extraction": {"timeout_seconds": 120}}
        with (
            patch("obsidian_import.config_from_overrides", return_value=MagicMock()) as mock_overrides,
            patch("obsidian_import.extract_text", return_value="hello"),
        ):
            convert_document(file_path=test_file, converters_config=overrides)

        mock_overrides.assert_called_once_with(overrides)


class TestObsidianImportErrorBoundary:
    def test_extraction_error_wrapped_as_document_conversion_error(self, tmp_path):
        """obsidian-import failures surface as the package's own boundary exception."""
        from obsidian_import.exceptions import ExtractionTimeoutError

        test_file = tmp_path / "big.xlsx"
        test_file.write_bytes(b"fake xlsx")

        with (
            patch("obsidian_import.config_from_overrides", return_value=MagicMock()),
            patch("obsidian_import.extract_text", side_effect=ExtractionTimeoutError("timed out after 120s")),
            pytest.raises(DocumentConversionError, match="big.xlsx"),
        ):
            convert_document(
                file_path=test_file,
                converters_config=SAMPLE_CONVERTERS_CONFIG,
            )
