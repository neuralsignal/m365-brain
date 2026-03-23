"""Public sync API — runs extractors against a Graph client.

CLI and web modes both import from here. This avoids cross-layer coupling
where web modules imported private functions from the CLI layer.
"""

from __future__ import annotations

from collections.abc import Callable

import click
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
from m365_extract.graph_client import GraphClient
from m365_extract.state import SyncState
from m365_extract.storage.base import StorageBackend

log = structlog.get_logger()

EXTRACTORS: dict[str, tuple] = {
    "email": (email, lambda cfg: cfg.extractors.email, False),
    "calendar": (calendar, lambda cfg: cfg.extractors.calendar, False),
    "teams_chats": (teams_chats, lambda cfg: cfg.extractors.teams_chats, False),
    "teams_channels": (teams_channels, lambda cfg: cfg.extractors.teams_channels, False),
    "onedrive": (onedrive, lambda cfg: cfg.extractors.onedrive, True),
    "sharepoint": (sharepoint, lambda cfg: cfg.extractors.sharepoint, True),
    "contacts": (contacts, lambda cfg: cfg.extractors.contacts, False),
    "directory": (directory, lambda cfg: cfg.extractors.directory, False),
}


def run_extractors(
    config: Config, token_provider: Callable[[], str], storage: StorageBackend, sync_state: SyncState, names: list[str]
) -> None:
    """Run enabled extractors once."""
    with GraphClient(config.graph, token_provider) as client:
        for ext_name in names:
            if ext_name not in EXTRACTORS:
                log.warning("sync.unknown_extractor", name=ext_name)
                continue

            module, config_getter, needs_converters = EXTRACTORS[ext_name]
            ext_config = config_getter(config)

            if not ext_config.enabled:
                log.info("sync.extractor_disabled", name=ext_name)
                continue

            log.info("sync.running_extractor", name=ext_name)
            state = sync_state.load(ext_name)

            try:
                if needs_converters:
                    updated_state, count = module.run(client, storage, state, ext_config, config.converters)
                else:
                    updated_state, count = module.run(client, storage, state, ext_config)
                sync_state.save(ext_name, updated_state)
                click.echo(f"  {ext_name}: {count} items written")
            except Exception as exc:
                log.error("sync.extractor_failed", name=ext_name, error=str(exc))
                click.echo(f"  {ext_name}: FAILED - {exc}")
