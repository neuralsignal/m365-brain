"""Tests for document converter."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from m365_extract.converters.document import convert_document

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
            patch("obsidian_import.config._load_default_yaml", return_value={}) as mock_defaults,
            patch("obsidian_import.config._deep_merge", return_value=SAMPLE_CONVERTERS_CONFIG) as mock_merge,
            patch("obsidian_import.config._build_config", return_value=mock_config) as mock_build,
            patch("obsidian_import.extract_text", return_value="# Converted") as mock_extract,
        ):
            result = convert_document(
                file_path=test_file,
                converters_config=SAMPLE_CONVERTERS_CONFIG,
            )

        assert result == "# Converted"
        mock_defaults.assert_called_once()
        mock_merge.assert_called_once_with({}, SAMPLE_CONVERTERS_CONFIG)
        mock_build.assert_called_once_with(SAMPLE_CONVERTERS_CONFIG, config_dir=None)
        mock_extract.assert_called_once_with(test_file, mock_config)

    def test_import_error_propagates(self, tmp_path):
        test_file = tmp_path / "test.pdf"
        test_file.write_bytes(b"fake pdf")

        with patch.dict("sys.modules", {"obsidian_import": None}), pytest.raises((ImportError, TypeError)):
            convert_document(
                file_path=test_file,
                converters_config=SAMPLE_CONVERTERS_CONFIG,
            )

    def test_empty_config_merges_with_defaults(self, tmp_path):
        test_file = tmp_path / "test.txt"
        test_file.write_bytes(b"hello")

        mock_config = MagicMock()
        defaults = {"extraction": {"timeout_seconds": 120}}
        with (
            patch("obsidian_import.config._load_default_yaml", return_value=defaults),
            patch("obsidian_import.config._deep_merge", return_value=defaults) as mock_merge,
            patch("obsidian_import.config._build_config", return_value=mock_config),
            patch("obsidian_import.extract_text", return_value="hello"),
        ):
            result = convert_document(
                file_path=test_file,
                converters_config={},
            )

        assert result == "hello"
        mock_merge.assert_called_once_with(defaults, {})
