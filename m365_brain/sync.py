"""Public sync API — runs extractors against a Graph client.

CLI and web modes both import from here. This avoids cross-layer coupling
where web modules imported private functions from the CLI layer.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from types import ModuleType
from typing import Any

import structlog

from m365_brain.config import Config, require_section
from m365_brain.index.catalog_storage import catalog_writes
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
from m365_brain.manifest import ChangeRecorder
from m365_brain.state import EXTRACTOR_STATE, StateStore
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


def build_context(config: Config, storage: StorageBackend, recorder: ChangeRecorder) -> ExtractorContext:
    """Assemble the context every extractor takes.

    `require_section` rather than a silent default: an extractor run against a
    config with no `vault:` section has nowhere to write, and inventing a
    layout here would scatter files under names no purge ever finds again.

    The recorder is passed in rather than made here because it belongs to one
    extractor's run: the caller wraps `storage` in a `RecordingStorage` over
    the same recorder, so deletes through `RemovalHandler` are recorded too.
    """
    paths = VaultPaths(require_section(config.vault, "vault"))
    return ExtractorContext(
        paths=paths,
        converters=config.converters.model_dump(),
        removal=RemovalHandler(storage=storage, paths=paths),
        recorder=recorder,
    )


class UnknownExtractor(Exception):
    """A name that is not one of the eight this package implements."""


def run_one(
    config: Config,
    client: GraphClient,
    storage: StorageBackend,
    ctx: ExtractorContext,
    state: dict,
    name: str,
) -> tuple[dict, int]:
    """Run one extractor and return `(updated state, items written)`.

    Raises rather than logging: the caller decides what a failure means. The
    cycle records it on the manifest and carries on to the next unit;
    `run_extractors` below logs and continues, which is the same policy spelled
    at a different level. One implementation of "how an extractor is called"
    either way -- the loop that wraps it is the part that varies.

    Because it is that one implementation, it is also where the file catalog is
    wired in. Both callers -- the cycle and the loop below -- reach an extractor
    through here, so wrapping the storage for the length of this call catalogs
    every binary written by every extractor, including one added tomorrow. The
    extractor name is the catalog's `source`, and it is in scope here and
    nowhere lower: `index` and `m365` are peers in the layer map and neither may
    import the other, so the join has to happen above both.
    """
    try:
        module, config_getter = EXTRACTORS[name]
    except KeyError:
        raise UnknownExtractor(f"no extractor named {name!r}; implemented: {sorted(EXTRACTORS)}") from None
    with catalog_writes(config.index, storage, name, lambda: datetime.now(UTC)) as cataloging:
        return module.run(client, cataloging, state, config_getter(config), ctx)


def run_extractors(
    config: Config, token_provider: Callable[[], str], storage: StorageBackend, state: StateStore, names: list[str]
) -> int:
    """Run the named extractors once. Returns total items synced.

    Trusts the caller's ``names`` list — does NOT filter by config.enabled.
    Callers (CLI, daemon) are responsible for deciding which extractors to run.
    """
    total_items = 0
    ctx = build_context(config, storage, ChangeRecorder())
    with GraphClient(config.graph, token_provider) as client:
        for ext_name in names:
            if ext_name not in EXTRACTORS:
                log.warning("sync.unknown_extractor", name=ext_name)
                continue

            log.info("sync.running_extractor", name=ext_name)
            try:
                updated_state, count = run_one(
                    config, client, storage, ctx, state.get(EXTRACTOR_STATE, ext_name), ext_name
                )
                state.put(EXTRACTOR_STATE, ext_name, updated_state)
                total_items += count
                log.info("sync.extractor_done", name=ext_name, items=count)
            except (GraphApiError, ExtractorError) as exc:
                log.error("sync.extractor_failed", name=ext_name, error=str(exc))
    return total_items
