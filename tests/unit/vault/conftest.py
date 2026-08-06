"""Vault fixtures.

Deliberately NOT the conventional layout. `paths.entry_file(d)` returning
`d/index.md` proves nothing about whether the name came from config, because
`index.md` is also what a hardcoded implementation would return. Every name
here is chosen so that a literal left in the code produces a visibly wrong
answer rather than a coincidentally right one.
"""

from __future__ import annotations

import pytest

from m365_brain.config import VaultConfig, VaultFilenames, VaultLayout
from m365_brain.vault.paths import VaultPaths

EXTRACTOR_DIRS = {
    "email": "mail",
    "calendar": "agenda",
    "contacts": "address-book",
    "directory": "staff",
    "onedrive": "my-files",
    "sharepoint": "team-files",
    "teams_chats": "chats",
    "teams_channels": "channels",
}


@pytest.fixture()
def layout() -> VaultLayout:
    return VaultLayout(
        inbox="incoming",
        annotations="notes",
        outbox="pending",
        meta="dot-meta",
        processed="done",
        failed="refused",
        inflight="claimed",
        state="cursors",
        manifests="runs",
    )


@pytest.fixture()
def vault_config(layout) -> VaultConfig:
    return VaultConfig(
        root="./store",
        layout=layout,
        extractor_dirs=dict(EXTRACTOR_DIRS),
        filenames=VaultFilenames(
            entry="page.md",
            conversation="thread.md",
            conversation_store="thread.ndjson",
            attachments="files",
            attachments_converted="files-as-text",
        ),
    )


@pytest.fixture()
def paths(vault_config) -> VaultPaths:
    return VaultPaths(vault_config)
