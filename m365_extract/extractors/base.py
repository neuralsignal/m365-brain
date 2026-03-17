"""Extractor protocol — all extractors implement this interface."""

from __future__ import annotations

from typing import Any, Protocol

from m365_extract.graph_client import GraphClient
from m365_extract.storage.base import StorageBackend


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
    ) -> tuple[dict, int]:
        """Run extraction. Returns (updated_state, items_written)."""
        ...
