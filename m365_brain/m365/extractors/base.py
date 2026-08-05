"""Extractor protocol — all extractors implement this interface."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from m365_brain.m365.client import GraphClient
from m365_brain.manifest import ChangeRecorder
from m365_brain.storage.base import StorageBackend
from m365_brain.vault.paths import VaultPaths
from m365_brain.vault.removal import RemovalHandler


@dataclass(frozen=True)
class ExtractorContext:
    """The cross-cutting dependencies every extractor takes.

    It exists so one signature serves all eight. `sync.py` used to carry a
    per-extractor `needs_converters` flag purely to choose between a 4-arg and
    a 5-arg call; a uniform shape is less code than the flag that dispatched
    around its absence.

    `converters` stays a raw config dict because that is what the document
    converters already consume — typing it is a separate change with its own
    test surface, and bundling it here would hide that.
    """

    paths: VaultPaths
    converters: dict
    removal: RemovalHandler
    recorder: ChangeRecorder
    """Where an extractor declares which upstream records it merged into a file
    it wrote. Only the two Teams extractors have anything to say -- every other
    write is captured by `RecordingStorage` without the extractor knowing."""


class Extractor(Protocol):
    """Protocol for Graph API data extractors."""

    name: str
    required_scopes: list[str]

    def run(
        self,
        client: GraphClient,
        storage: StorageBackend,
        state: dict,
        config: Any,
        ctx: ExtractorContext,
    ) -> tuple[dict, int]:
        """Run extraction. Returns (updated_state, items_written)."""
        ...
