"""Public sync API — runs extractors against a Graph client.

CLI and web modes both import from here. This avoids cross-layer coupling
where web modules imported private functions from the CLI layer.
"""

from __future__ import annotations

from collections.abc import Callable
from types import ModuleType
from typing import Any

import structlog

from m365_brain.config import Config, require_section
from m365_brain.m365.client import GraphApiError, GraphClient
from m365_brain.m365.extractors import (
    calendar,
    contacts,
    directory,
    email,
    onedrive,
    sharepoint,
    teams_channels,
    teams_chats,
)
from m365_brain.m365.extractors.base import ExtractorContext
from m365_brain.m365.extractors.errors import ExtractorError
from m365_brain.state import SyncState
from m365_brain.storage.base import StorageBackend
from m365_brain.vault.paths import VaultPaths
from m365_brain.vault.removal import RemovalHandler

log = structlog.get_logger()

type ExtractorEntry = tuple[ModuleType, Callable[[Config], Any]]

# The third element used to be a `needs_converters` bool, read once to choose
# between a 4-arg and a 5-arg call. `ExtractorContext` made every extractor take
# the same five arguments, so the flag and the branch it fed both went away.
EXTRACTORS: dict[str, ExtractorEntry] = {
    "email": (email, lambda cfg: cfg.extractors.email),
    "calendar": (calendar, lambda cfg: cfg.extractors.calendar),
    "teams_chats": (teams_chats, lambda cfg: cfg.extractors.teams_chats),
    "teams_channels": (teams_channels, lambda cfg: cfg.extractors.teams_channels),
    "onedrive": (onedrive, lambda cfg: cfg.extractors.onedrive),
    "sharepoint": (sharepoint, lambda cfg: cfg.extractors.sharepoint),
    "contacts": (contacts, lambda cfg: cfg.extractors.contacts),
    "directory": (directory, lambda cfg: cfg.extractors.directory),
}


def build_context(config: Config, storage: StorageBackend) -> ExtractorContext:
    """Assemble the context every extractor takes.

    `require_section` rather than a silent default: an extractor run against a
    config with no `vault:` section has nowhere to write, and inventing a
    layout here would scatter files under names no purge ever finds again.
    """
    paths = VaultPaths(require_section(config.vault, "vault"))
    return ExtractorContext(
        paths=paths,
        converters=config.converters.model_dump(),
        removal=RemovalHandler(storage=storage, paths=paths),
    )


def run_extractors(
    config: Config, token_provider: Callable[[], str], storage: StorageBackend, sync_state: SyncState, names: list[str]
) -> int:
    """Run the named extractors once. Returns total items synced.

    Trusts the caller's ``names`` list — does NOT filter by config.enabled.
    Callers (CLI, daemon) are responsible for deciding which extractors to run.
    """
    total_items = 0
    ctx = build_context(config, storage)
    with GraphClient(config.graph, token_provider) as client:
        for ext_name in names:
            if ext_name not in EXTRACTORS:
                log.warning("sync.unknown_extractor", name=ext_name)
                continue

            module, config_getter = EXTRACTORS[ext_name]
            ext_config = config_getter(config)

            log.info("sync.running_extractor", name=ext_name)
            state = sync_state.load(ext_name)

            try:
                updated_state, count = module.run(client, storage, state, ext_config, ctx)
                sync_state.save(ext_name, updated_state)
                total_items += count
                log.info("sync.extractor_done", name=ext_name, items=count)
            except (GraphApiError, ExtractorError) as exc:
                log.error("sync.extractor_failed", name=ext_name, error=str(exc))
    return total_items
