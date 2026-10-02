"""Tests for file-processing dataclasses."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
from unittest.mock import MagicMock

import pytest

from m365_brain.m365.extractors._file_types import (
    DriveItemMetadata,
    FileProcessingConfig,
    FileProcessingContext,
)


class TestFileProcessingConfig:
    def test_frozen(self) -> None:
        cfg = FileProcessingConfig(
            eager_patterns=["*.docx"],
            convertible_extensions=[".docx"],
            max_file_size_mb=50,
            converters_config={},
        )
        with pytest.raises(FrozenInstanceError):
            cfg.max_file_size_mb = 100  # type: ignore[misc]

    def test_fields(self) -> None:
        names = {f.name for f in fields(FileProcessingConfig)}
        assert names == {"eager_patterns", "convertible_extensions", "max_file_size_mb", "converters_config"}


class TestFileProcessingContext:
    def test_frozen(self) -> None:
        ctx = FileProcessingContext(
            client=MagicMock(),
            storage=MagicMock(),
            file_config=MagicMock(),
            removal=MagicMock(),
            extractor="onedrive",
        )
        with pytest.raises(FrozenInstanceError):
            ctx.extractor = "sharepoint"  # type: ignore[misc]

    def test_fields(self) -> None:
        names = {f.name for f in fields(FileProcessingContext)}
        assert names == {"client", "storage", "file_config", "removal", "extractor"}


class TestDriveItemMetadata:
    def test_frozen(self) -> None:
        meta = DriveItemMetadata(
            file_name="doc.pdf",
            item_id="abc123",
            size=1024,
            modified_time="2024-01-01T00:00:00Z",
            modified_by="Alice",
            parent_path="Documents",
            web_url="https://example.com",
        )
        with pytest.raises(FrozenInstanceError):
            meta.file_name = "other.pdf"  # type: ignore[misc]

    def test_fields(self) -> None:
        names = {f.name for f in fields(DriveItemMetadata)}
        assert names == {"file_name", "item_id", "size", "modified_time", "modified_by", "parent_path", "web_url"}
