"""Gate 4 -- every store-touching test runs against both implementations.

`store` is parametrised, unconditionally. No test may skip a parameter and no
test may branch on which one it got: the moment one does, the fake stops being
evidence about the real store and becomes a second, untested implementation
that happens to satisfy the same tests.

The layout is deliberately non-conventional, for the same reason the vault
fixtures are: a filesystem store that returned `_processed/abc.md` would look
right against a default layout whether or not it read the config.
"""

from __future__ import annotations

import json

import pytest

from m365_brain.config import (
    AuthProfileConfig,
    EmailOutboxConfig,
    EmailSignatureConfig,
    OutboxDefinitionConfig,
    OutboxesConfig,
    ReconcileConfig,
    VaultConfig,
    VaultFilenames,
    VaultLayout,
)
from m365_brain.outbox.filesystem_store import FilesystemIntentStore
from m365_brain.outbox.stores import InMemoryIntentStore
from m365_brain.storage.local import LocalBackend
from m365_brain.vault.paths import VaultPaths

OUTBOX_NAMES = ("email.draft", "email.reply", "teams.post_message")

QUOTE_MARKERS = [r"^\s*From:\s", r"^\s*Von:\s", r"^\s*On .+ wrote:\s*$", r"^-{4,}\s*$"]


@pytest.fixture()
def vault_config() -> VaultConfig:
    return VaultConfig(
        root="./store",
        layout=VaultLayout(
            inbox="incoming",
            annotations="notes",
            outbox="pending",
            meta="dot-meta",
            processed="done",
            rejected="refused",
            inflight="claimed",
            state="cursors",
            manifests="runs",
        ),
        extractor_dirs={
            "email": "mail",
            "calendar": "agenda",
            "contacts": "address-book",
            "directory": "staff",
            "onedrive": "my-files",
            "sharepoint": "team-files",
            "teams_chats": "chats",
            "teams_channels": "channels",
        },
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


@pytest.fixture(params=["filesystem", "memory"])
def store(request, tmp_path, paths):
    if request.param == "filesystem":
        return FilesystemIntentStore(LocalBackend(str(tmp_path / "vault")), paths, OUTBOX_NAMES)
    return InMemoryIntentStore()


@pytest.fixture()
def outboxes_config(tmp_path) -> OutboxesConfig:
    return OutboxesConfig(
        attachment_root=str(tmp_path / "assets"),
        forbidden_send_scopes=["Mail.Send"],
        definitions={
            "email.draft": OutboxDefinitionConfig(tier="draft_only", auth_profile="mail"),
            "teams.post_message": OutboxDefinitionConfig(tier="auto_send", auth_profile="teams"),
        },
        email=EmailOutboxConfig(
            signature=EmailSignatureConfig(html_path=None, logo_path=None, logo_content_id="brand_logo")
        ),
        reconcile=ReconcileConfig(quote_markers=QUOTE_MARKERS),
    )


@pytest.fixture()
def auth_profiles(tmp_path) -> dict[str, AuthProfileConfig]:
    return {
        "mail": AuthProfileConfig(
            client_id="mail-app",
            tenant_id="t",
            scopes=["Mail.ReadWrite"],
            token_cache_path=str(tmp_path / "mail.json"),
            client_secret=None,
        ),
        "teams": AuthProfileConfig(
            client_id="teams-app",
            tenant_id="t",
            scopes=["ChannelMessage.Send"],
            token_cache_path=str(tmp_path / "teams.json"),
            client_secret=None,
        ),
    }


DRAFT_PAYLOAD = {
    "kind": "email.draft",
    "mailbox": "me",
    "to": ["a@example.com"],
    "cc": None,
    "bcc": None,
    "subject": "Hello",
    "attachments": None,
    "inline_images": None,
    "include_signature": True,
    "revises_message_id": None,
}


def intent_markdown(uuid: str, payload: dict, body: str) -> str:
    """Compose an intent file the way an authoring agent would."""
    lines = [
        "---",
        f"uuid: {uuid}",
        "schema_version: 1",
        "created_at: 2026-08-05T09:00:00Z",
        "created_by: test",
        "payload:",
        *(f"  {line}" for line in json.dumps(payload, indent=2).splitlines()),
        "---",
        body,
    ]
    return "\n".join(lines)


@pytest.fixture()
def place(store):
    """Put an intent into whichever store the test got."""

    def _place(
        uuid: str, outbox_name: str = "email.draft", payload: dict | None = None, body: str = "Hi there."
    ) -> str:
        content = intent_markdown(uuid, payload or DRAFT_PAYLOAD, body)
        store.put(outbox_name, uuid, content)
        return content

    return _place
