"""Public sync API — runs extractors against a Graph client.

CLI and web modes both import from here. This avoids cross-layer coupling
where web modules imported private functions from the CLI layer.
"""

from __future__ import annotations

from collections.abc import Callable
from types import ModuleType
from typing import Any

import structlog

from m365_extract.config import Config
from m365_extract.extractors import (
    calendar,
    contacts,
    directory,
    email,
    onedrive,
    sharepoint,
    teams_channels,
    teams_chats,
)
from m365_extract.extractors.errors import ExtractorError
from m365_extract.graph_client import GraphApiError, GraphClient
from m365_extract.state import SyncState
from m365_extract.storage.base import StorageBackend

log = structlog.get_logger()

type ExtractorEntry = tuple[ModuleType, Callable[[Config], Any], bool]

EXTRACTORS: dict[str, ExtractorEntry] = {
    "email": (email, lambda cfg: cfg.extractors.email, True),
    "calendar": (calendar, lambda cfg: cfg.extractors.calendar, False),
    "teams_chats": (teams_chats, lambda cfg: cfg.extractors.teams_chats, True),
    "teams_channels": (teams_channels, lambda cfg: cfg.extractors.teams_channels, False),
    "onedrive": (onedrive, lambda cfg: cfg.extractors.onedrive, True),
    "sharepoint": (sharepoint, lambda cfg: cfg.extractors.sharepoint, True),
    "contacts": (contacts, lambda cfg: cfg.extractors.contacts, False),
    "directory": (directory, lambda cfg: cfg.extractors.directory, False),
}


def run_extractors(
    config: Config, token_provider: Callable[[], str], storage: StorageBackend, sync_state: SyncState, names: list[str]
) -> int:
    """Run the named extractors once. Returns total items synced.

    Trusts the caller's ``names`` list — does NOT filter by config.enabled.
    Callers (CLI, daemon) are responsible for deciding which extractors to run.
    """
    total_items = 0
    with GraphClient(config.graph, token_provider) as client:
        for ext_name in names:
            if ext_name not in EXTRACTORS:
                log.warning("sync.unknown_extractor", name=ext_name)
                continue

            module, config_getter, needs_converters = EXTRACTORS[ext_name]
            ext_config = config_getter(config)

            log.info("sync.running_extractor", name=ext_name)
            state = sync_state.load(ext_name)

            try:
                if needs_converters:
                    updated_state, count = module.run(
                        client, storage, state, ext_config, config.converters.model_dump()
                    )
                else:
                    updated_state, count = module.run(client, storage, state, ext_config)
                sync_state.save(ext_name, updated_state)
                total_items += count
                log.info("sync.extractor_done", name=ext_name, items=count)
            except (GraphApiError, ExtractorError) as exc:
                log.error("sync.extractor_failed", name=ext_name, error=str(exc))
    return total_items
