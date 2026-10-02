"""Dataclasses for file-based extractor configuration and metadata."""

from __future__ import annotations

from dataclasses import dataclass

from m365_brain.m365.client import GraphClient
from m365_brain.storage.base import StorageBackend
from m365_brain.vault.removal import RemovalHandler


@dataclass(frozen=True)
class FileProcessingConfig:
    """Groups file-processing parameters passed together through extractors."""

    eager_patterns: list[str]
    convertible_extensions: list[str]
    max_file_size_mb: int
    converters_config: dict


@dataclass(frozen=True)
class FileProcessingContext:
    """Per-run context grouping I/O dependencies with file-processing config.

    `removal` and `extractor` travel together because a delete is only
    attributable with the extractor name; the canonical handler logs it.
    """

    client: GraphClient
    storage: StorageBackend
    file_config: FileProcessingConfig
    removal: RemovalHandler
    extractor: str


@dataclass(frozen=True)
class DriveItemMetadata:
    """Common metadata extracted from a Graph API drive item."""

    file_name: str
    item_id: str
    size: int
    modified_time: str
    modified_by: str
    parent_path: str
    web_url: str
