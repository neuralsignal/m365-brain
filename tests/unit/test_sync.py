"""Tests for m365_brain.sync module."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from m365_brain.config import VaultConfig, VaultFilenames, VaultLayout
from m365_brain.config.errors import ConfigError
from m365_brain.m365.extractors.base import ExtractorContext
from m365_brain.m365.extractors.errors import ExtractorError
from m365_brain.sync import EXTRACTORS, run_extractors

VAULT = VaultConfig(
    root="./vault",
    layout=VaultLayout(
        inbox="inbox",
        annotations="annotations",
        outbox="outbox",
        meta="_meta",
        processed="_processed",
        rejected="_rejected",
        inflight="_inflight",
        state="state",
        manifests="manifests",
    ),
    extractor_dirs={
        "email": "emails",
        "calendar": "calendar",
        "contacts": "contacts",
        "directory": "directory",
        "onedrive": "onedrive",
        "sharepoint": "sharepoint",
        "teams_chats": "teams-chats",
        "teams_channels": "teams-channels",
    },
    filenames=VaultFilenames(
        entry="index.md",
        conversation="messages.md",
        conversation_store="messages.jsonl",
        attachments="attachments",
        attachments_converted="attachments_converted",
    ),
)


@pytest.fixture()
def vaulted_config(full_config):
    """`build_context` requires a `vault:` section; the shared `full_config` has none."""
    return full_config.model_copy(update={"vault": VAULT})


def _make_mock_extractor(return_value: tuple = ({}, 0), side_effect: Exception | None = None) -> MagicMock:
    """Create a mock extractor module with a run function."""
    mod = MagicMock()
    if side_effect:
        mod.run.side_effect = side_effect
    else:
        mod.run.return_value = return_value
    return mod


def _patch_sync(target: str) -> patch:
    return patch(f"m365_brain.sync.{target}")


class TestRunExtractors:
    def test_runs_all_named_extractors_regardless_of_config_enabled(self, vaulted_config):
        """run_extractors trusts the caller's names list — even disabled-in-config extractors run."""
        mock_mod = _make_mock_extractor(return_value=({}, 3))
        original = EXTRACTORS["teams_channels"]
        mock_state = MagicMock()
        mock_state.load.return_value = {}
        mock_storage = MagicMock()

        with (
            _patch_sync("GraphClient") as mock_gc,
            patch.dict(EXTRACTORS, {"teams_channels": (mock_mod, original[1])}),
        ):
            mock_client = MagicMock()
            mock_gc.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_gc.return_value.__exit__ = MagicMock(return_value=False)

            total = run_extractors(vaulted_config, lambda: "token", mock_storage, mock_state, ["teams_channels"])

        mock_mod.run.assert_called_once()
        mock_state.save.assert_called_once()
        assert total == 3

    def test_warns_on_unknown_extractor(self, vaulted_config):
        mock_state = MagicMock()
        mock_state.load.return_value = {}
        mock_storage = MagicMock()

        with (
            _patch_sync("GraphClient") as mock_gc,
            _patch_sync("log") as mock_log,
        ):
            mock_client = MagicMock()
            mock_gc.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_gc.return_value.__exit__ = MagicMock(return_value=False)

            run_extractors(vaulted_config, lambda: "token", mock_storage, mock_state, ["nonexistent"])
            mock_log.warning.assert_called_once_with("sync.unknown_extractor", name="nonexistent")

    def test_handles_extractor_exception(self, vaulted_config):
        mock_mod = _make_mock_extractor(side_effect=ExtractorError("API down"))
        original = EXTRACTORS["email"]
        mock_state = MagicMock()
        mock_state.load.return_value = {}
        mock_storage = MagicMock()

        with (
            _patch_sync("GraphClient") as mock_gc,
            patch.dict(EXTRACTORS, {"email": (mock_mod, original[1])}),
        ):
            mock_client = MagicMock()
            mock_gc.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_gc.return_value.__exit__ = MagicMock(return_value=False)

            run_extractors(vaulted_config, lambda: "token", mock_storage, mock_state, ["email"])

        mock_state.save.assert_not_called()

    def test_missing_vault_section_raises_config_error(self, full_config):
        """No `vault:` means nowhere to write; `build_context` names the missing key."""
        mock_state = MagicMock()
        mock_state.load.return_value = {}

        with pytest.raises(ConfigError, match="vault"):
            run_extractors(full_config, lambda: "token", MagicMock(), mock_state, ["email"])

    def test_passes_context_carrying_converters(self, vaulted_config):
        od_config = vaulted_config.extractors.onedrive.model_copy(update={"enabled": True})
        extractors = vaulted_config.extractors.model_copy(update={"onedrive": od_config})
        config = vaulted_config.model_copy(update={"extractors": extractors})

        mock_mod = _make_mock_extractor(return_value=({}, 5))
        original = EXTRACTORS["onedrive"]
        mock_state = MagicMock()
        mock_state.load.return_value = {}
        mock_storage = MagicMock()

        with (
            _patch_sync("GraphClient") as mock_gc,
            patch.dict(EXTRACTORS, {"onedrive": (mock_mod, original[1])}),
        ):
            mock_client = MagicMock()
            mock_gc.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_gc.return_value.__exit__ = MagicMock(return_value=False)

            run_extractors(config, lambda: "token", mock_storage, mock_state, ["onedrive"])

        mock_mod.run.assert_called_once()
        ctx = mock_mod.run.call_args[0][4]
        assert isinstance(ctx, ExtractorContext)
        assert ctx.converters == config.converters.model_dump()
        assert ctx.paths.vault is VAULT
        assert ctx.removal.storage is mock_storage

    def test_every_extractor_gets_the_same_five_args(self, vaulted_config):
        """The `needs_converters` flag is gone — a non-converting extractor takes the same ctx."""
        mock_mod = _make_mock_extractor(return_value=({}, 2))
        original = EXTRACTORS["contacts"]
        mock_state = MagicMock()
        mock_state.load.return_value = {}
        mock_storage = MagicMock()

        with (
            _patch_sync("GraphClient") as mock_gc,
            patch.dict(EXTRACTORS, {"contacts": (mock_mod, original[1])}),
        ):
            mock_client = MagicMock()
            mock_gc.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_gc.return_value.__exit__ = MagicMock(return_value=False)

            total = run_extractors(vaulted_config, lambda: "token", mock_storage, mock_state, ["contacts"])

        mock_mod.run.assert_called_once()
        call_args = mock_mod.run.call_args[0]
        assert len(call_args) == 5
        assert isinstance(call_args[4], ExtractorContext)
        assert total == 2

    def test_successful_run_saves_state(self, vaulted_config):
        mock_mod = _make_mock_extractor(return_value=({"delta": "abc"}, 7))
        original = EXTRACTORS["email"]
        mock_state = MagicMock()
        mock_state.load.return_value = {}
        mock_storage = MagicMock()

        with (
            _patch_sync("GraphClient") as mock_gc,
            patch.dict(EXTRACTORS, {"email": (mock_mod, original[1])}),
        ):
            mock_client = MagicMock()
            mock_gc.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_gc.return_value.__exit__ = MagicMock(return_value=False)

            run_extractors(vaulted_config, lambda: "token", mock_storage, mock_state, ["email"])

        mock_state.save.assert_called_once_with("email", {"delta": "abc"})

    def test_extractors_dict_has_all_expected_keys(self):
        expected = {
            "email",
            "calendar",
            "teams_chats",
            "teams_channels",
            "onedrive",
            "sharepoint",
            "contacts",
            "directory",
        }
        assert set(EXTRACTORS.keys()) == expected

    def test_extractor_entries_are_module_and_config_getter_pairs(self):
        """Two elements, not three — the `needs_converters` flag was removed."""
        assert all(len(entry) == 2 for entry in EXTRACTORS.values())
