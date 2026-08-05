"""Vault wiring for the extractor tests.

Two layouts, on purpose.

`vault_config` is the conventional one, and the `ctx` fixture built on it is
what the existing suite uses: those tests assert rendering, delta handling and
error containment, and a renamed directory would only add noise to them.

`odd_vault_config` renames every single directory and file. The golden tests use
it, because a fixture asserted against the conventional names would still pass
with the literals hardcoded — which is exactly the regression the vault work
exists to prevent. If a test can tell you the layout is configurable, it has to
be a test that would fail if it were not.
"""

from __future__ import annotations

import pytest

from m365_brain.config import VaultConfig, VaultFilenames, VaultLayout
from m365_brain.m365.extractors.base import ExtractorContext
from m365_brain.vault.paths import VaultPaths
from m365_brain.vault.removal import RemovalHandler

NO_CONVERTERS: dict = {}

CONVENTIONAL_EXTRACTOR_DIRS = {
    "email": "emails",
    "calendar": "calendar",
    "contacts": "contacts",
    "directory": "directory",
    "onedrive": "onedrive",
    "sharepoint": "sharepoint",
    "teams_chats": "teams-chats",
    "teams_channels": "teams-channels",
}

ODD_EXTRACTOR_DIRS = {
    "email": "mail",
    "calendar": "agenda",
    "contacts": "address-book",
    "directory": "staff",
    "onedrive": "my-files",
    "sharepoint": "team-files",
    "teams_chats": "chats",
    "teams_channels": "channels",
}


def _layout(prefix: str) -> VaultLayout:
    return VaultLayout(
        inbox=f"{prefix}inbox",
        annotations=f"{prefix}annotations",
        outbox=f"{prefix}outbox",
        meta=f"{prefix}_meta",
        processed=f"{prefix}_processed",
        rejected=f"{prefix}_rejected",
        inflight=f"{prefix}_inflight",
        state=f"{prefix}state",
        manifests=f"{prefix}manifests",
    )


@pytest.fixture()
def vault_config() -> VaultConfig:
    """The conventional layout, matching config/m365-brain.example.yaml."""
    return VaultConfig(
        root="./vault",
        layout=_layout(""),
        extractor_dirs=dict(CONVENTIONAL_EXTRACTOR_DIRS),
        filenames=VaultFilenames(
            entry="index.md",
            conversation="messages.md",
            conversation_store="messages.jsonl",
            attachments="attachments",
            attachments_converted="attachments_converted",
        ),
    )


@pytest.fixture()
def odd_vault_config() -> VaultConfig:
    """Nothing here shares a name with the conventional layout. That is the test."""
    return VaultConfig(
        root="./store",
        layout=_layout("zz-"),
        extractor_dirs=dict(ODD_EXTRACTOR_DIRS),
        filenames=VaultFilenames(
            entry="page.md",
            conversation="thread.md",
            conversation_store="thread.ndjson",
            attachments="files",
            attachments_converted="files-as-text",
        ),
    )


@pytest.fixture()
def vault_paths(vault_config) -> VaultPaths:
    return VaultPaths(vault_config)


@pytest.fixture()
def odd_vault_paths(odd_vault_config) -> VaultPaths:
    return VaultPaths(odd_vault_config)


def make_ctx(paths: VaultPaths, storage, converters: dict) -> ExtractorContext:
    """Build a context without a fixture, for tests that need a second one."""
    return ExtractorContext(
        paths=paths,
        converters=converters,
        removal=RemovalHandler(storage=storage, paths=paths),
    )


@pytest.fixture()
def ctx(vault_paths, local_storage) -> ExtractorContext:
    """Conventional layout, converters disabled — what most extractor tests want."""
    return make_ctx(vault_paths, local_storage, NO_CONVERTERS)


@pytest.fixture()
def odd_ctx(odd_vault_paths, local_storage) -> ExtractorContext:
    return make_ctx(odd_vault_paths, local_storage, NO_CONVERTERS)
