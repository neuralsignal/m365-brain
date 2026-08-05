"""Tests for the shared Teams extractor context struct."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from unittest.mock import MagicMock

import pytest

from m365_brain.m365.client import GraphClient
from m365_brain.m365.extractors._teams_context import TeamsContext
from m365_brain.storage.local import LocalBackend
from m365_brain.vault.paths import VaultPaths


def _ctx(local_storage: LocalBackend, paths: VaultPaths) -> TeamsContext:
    return TeamsContext(
        client=MagicMock(spec=GraphClient),
        storage=local_storage,
        settings=MagicMock(),
        converters_config={"backends": {"pdf": "markitdown"}},
        failed_attachments={"msg-1:a.pdf": "http_403"},
        conv_dir=paths.inbox_item("teams_chats", "foo_abc"),
        paths=paths,
    )


class TestTeamsContext:
    def test_fields_are_wired_through_unchanged(self, local_storage: LocalBackend, vault_paths: VaultPaths) -> None:
        ctx = _ctx(local_storage, vault_paths)
        assert ctx.storage is local_storage
        assert ctx.paths is vault_paths
        assert ctx.conv_dir == "inbox/teams-chats/foo_abc"
        assert ctx.converters_config == {"backends": {"pdf": "markitdown"}}
        assert ctx.failed_attachments == {"msg-1:a.pdf": "http_403"}

    def test_rebinding_a_field_is_rejected(self, local_storage: LocalBackend, vault_paths: VaultPaths) -> None:
        ctx = _ctx(local_storage, vault_paths)
        with pytest.raises(FrozenInstanceError):
            ctx.conv_dir = "inbox/teams-chats/other"

    def test_skip_list_stays_shared_with_the_caller(self, local_storage: LocalBackend, vault_paths: VaultPaths) -> None:
        """Frozen binds the reference, not the dict — the extractors mutate the skip list in place."""
        failed: dict[str, str] = {}
        ctx = TeamsContext(
            client=MagicMock(spec=GraphClient),
            storage=local_storage,
            settings=MagicMock(),
            converters_config={},
            failed_attachments=failed,
            conv_dir=vault_paths.inbox_item("teams_channels", "team", "chan_abc"),
            paths=vault_paths,
        )
        ctx.failed_attachments["msg-2:b.pdf"] = "http_404"
        assert failed == {"msg-2:b.pdf": "http_404"}
