"""Tests for m365_extract.sync module."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from m365_extract.extractors.errors import ExtractorError
from m365_extract.sync import EXTRACTORS, run_extractors


def _make_mock_extractor(return_value: tuple = ({}, 0), side_effect: Exception | None = None) -> MagicMock:
    """Create a mock extractor module with a run function."""
    mod = MagicMock()
    if side_effect:
        mod.run.side_effect = side_effect
    else:
        mod.run.return_value = return_value
    return mod


def _patch_sync(target: str) -> patch:
    return patch(f"m365_extract.sync.{target}")


class TestRunExtractors:
    def test_runs_all_named_extractors_regardless_of_config_enabled(self, full_config):
        """run_extractors trusts the caller's names list — even disabled-in-config extractors run."""
        mock_mod = _make_mock_extractor(return_value=({}, 3))
        original = EXTRACTORS["teams_channels"]
        mock_state = MagicMock()
        mock_state.load.return_value = {}
        mock_storage = MagicMock()

        with (
            _patch_sync("GraphClient") as mock_gc,
            patch.dict(EXTRACTORS, {"teams_channels": (mock_mod, original[1], original[2])}),
        ):
            mock_client = MagicMock()
            mock_gc.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_gc.return_value.__exit__ = MagicMock(return_value=False)

            total = run_extractors(full_config, lambda: "token", mock_storage, mock_state, ["teams_channels"])

        mock_mod.run.assert_called_once()
        mock_state.save.assert_called_once()
        assert total == 3

    def test_warns_on_unknown_extractor(self, full_config):
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

            run_extractors(full_config, lambda: "token", mock_storage, mock_state, ["nonexistent"])
            mock_log.warning.assert_called_once_with("sync.unknown_extractor", name="nonexistent")

    def test_handles_extractor_exception(self, full_config):
        mock_mod = _make_mock_extractor(side_effect=ExtractorError("API down"))
        original = EXTRACTORS["email"]
        mock_state = MagicMock()
        mock_state.load.return_value = {}
        mock_storage = MagicMock()

        with (
            _patch_sync("GraphClient") as mock_gc,
            patch.dict(EXTRACTORS, {"email": (mock_mod, original[1], original[2])}),
        ):
            mock_client = MagicMock()
            mock_gc.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_gc.return_value.__exit__ = MagicMock(return_value=False)

            run_extractors(full_config, lambda: "token", mock_storage, mock_state, ["email"])

        mock_state.save.assert_not_called()

    def test_passes_converters_when_needed(self, full_config):
        od_config = full_config.extractors.onedrive.model_copy(update={"enabled": True})
        extractors = full_config.extractors.model_copy(update={"onedrive": od_config})
        config = full_config.model_copy(update={"extractors": extractors})

        mock_mod = _make_mock_extractor(return_value=({}, 5))
        original = EXTRACTORS["onedrive"]
        mock_state = MagicMock()
        mock_state.load.return_value = {}
        mock_storage = MagicMock()

        with (
            _patch_sync("GraphClient") as mock_gc,
            patch.dict(EXTRACTORS, {"onedrive": (mock_mod, original[1], original[2])}),
        ):
            mock_client = MagicMock()
            mock_gc.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_gc.return_value.__exit__ = MagicMock(return_value=False)

            run_extractors(config, lambda: "token", mock_storage, mock_state, ["onedrive"])

        mock_mod.run.assert_called_once()
        call_args = mock_mod.run.call_args[0]
        assert call_args[4] == config.converters.model_dump()

    def test_omits_converters_when_not_needed(self, full_config):
        mock_mod = _make_mock_extractor(return_value=({}, 2))
        original = EXTRACTORS["contacts"]
        mock_state = MagicMock()
        mock_state.load.return_value = {}
        mock_storage = MagicMock()

        with (
            _patch_sync("GraphClient") as mock_gc,
            patch.dict(EXTRACTORS, {"contacts": (mock_mod, original[1], original[2])}),
        ):
            mock_client = MagicMock()
            mock_gc.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_gc.return_value.__exit__ = MagicMock(return_value=False)

            total = run_extractors(full_config, lambda: "token", mock_storage, mock_state, ["contacts"])

        mock_mod.run.assert_called_once()
        call_args = mock_mod.run.call_args[0]
        assert len(call_args) == 4
        assert total == 2

    def test_successful_run_saves_state(self, full_config):
        mock_mod = _make_mock_extractor(return_value=({"delta": "abc"}, 7))
        original = EXTRACTORS["email"]
        mock_state = MagicMock()
        mock_state.load.return_value = {}
        mock_storage = MagicMock()

        with (
            _patch_sync("GraphClient") as mock_gc,
            patch.dict(EXTRACTORS, {"email": (mock_mod, original[1], original[2])}),
        ):
            mock_client = MagicMock()
            mock_gc.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_gc.return_value.__exit__ = MagicMock(return_value=False)

            run_extractors(full_config, lambda: "token", mock_storage, mock_state, ["email"])

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
