"""Tests for m365_extract.cli module."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import MagicMock, patch

import click.testing
import pytest

from m365_extract.cli import _EXTRACTORS, main


@pytest.fixture()
def runner():
    return click.testing.CliRunner()


@pytest.fixture()
def config_file(tmp_path):
    """Create a dummy config file so --config path validation passes."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text("dummy: true")
    return str(cfg)


def _patch_cli(target: str) -> patch:
    return patch(f"m365_extract.cli.{target}")


def _make_mock_extractor(return_value: tuple = ({}, 0), side_effect: Exception | None = None) -> MagicMock:
    """Create a mock extractor module with a run function."""
    mod = MagicMock()
    if side_effect:
        mod.run.side_effect = side_effect
    else:
        mod.run.return_value = return_value
    return mod


def _standard_patches():
    """Return context managers for the standard CLI dependencies."""
    return (
        _patch_cli("load_config"),
        _patch_cli("make_cli_token_provider"),
        _patch_cli("create_storage"),
        _patch_cli("SyncState"),
        _patch_cli("GraphClient"),
        _patch_cli("structlog"),
    )


def _setup_mocks(mock_load, mock_state_cls, mock_gc, config):
    """Configure standard mock return values."""
    mock_load.return_value = config
    mock_state = MagicMock()
    mock_state.load.return_value = {}
    mock_state_cls.return_value = mock_state
    mock_client = MagicMock()
    mock_gc.return_value.__enter__ = MagicMock(return_value=mock_client)
    mock_gc.return_value.__exit__ = MagicMock(return_value=False)
    return mock_state, mock_client


class TestSyncCommand:
    def test_sync_requires_once_or_continuous(self, runner, config_file):
        result = runner.invoke(main, ["--config", config_file, "sync"])
        assert result.exit_code != 0
        assert "Specify either --once or --continuous" in result.output

    def test_sync_once_calls_run_extractors(self, runner, config_file, full_config):
        with (
            _patch_cli("load_config") as mock_load,
            _patch_cli("make_cli_token_provider"),
            _patch_cli("create_storage"),
            _patch_cli("SyncState"),
            _patch_cli("_run_extractors") as mock_run,
            _patch_cli("structlog"),
        ):
            mock_load.return_value = full_config

            result = runner.invoke(main, ["--config", config_file, "sync", "--once"])

            assert result.exit_code == 0
            mock_run.assert_called_once()
            assert mock_run.call_args[0][0] is full_config

    def test_sync_continuous_calls_run_continuous(self, runner, config_file, full_config):
        with (
            _patch_cli("load_config") as mock_load,
            _patch_cli("make_cli_token_provider"),
            _patch_cli("create_storage"),
            _patch_cli("SyncState"),
            _patch_cli("_run_continuous") as mock_run,
            _patch_cli("structlog"),
        ):
            mock_load.return_value = full_config

            result = runner.invoke(main, ["--config", config_file, "sync", "--continuous"])

            assert result.exit_code == 0
            mock_run.assert_called_once()

    def test_sync_once_with_extractor_filter(self, runner, config_file, full_config):
        with (
            _patch_cli("load_config") as mock_load,
            _patch_cli("make_cli_token_provider"),
            _patch_cli("create_storage"),
            _patch_cli("SyncState"),
            _patch_cli("_run_extractors") as mock_run,
            _patch_cli("structlog"),
        ):
            mock_load.return_value = full_config

            result = runner.invoke(main, ["--config", config_file, "sync", "--once", "--extractors", "email,calendar"])

            assert result.exit_code == 0
            names_arg = mock_run.call_args[0][4]
            assert names_arg == ["email", "calendar"]


class TestRunExtractors:
    def test_skips_disabled_extractors(self, runner, config_file, full_config):
        """teams_channels is disabled in full_config; verify module.run is not called."""
        args = ["--config", config_file, "sync", "--once", "--extractors", "teams_channels"]
        with (
            _patch_cli("load_config") as mock_load,
            _patch_cli("make_cli_token_provider"),
            _patch_cli("create_storage"),
            _patch_cli("SyncState") as mock_state_cls,
            _patch_cli("GraphClient") as mock_gc,
            _patch_cli("structlog"),
        ):
            mock_state, _ = _setup_mocks(mock_load, mock_state_cls, mock_gc, full_config)
            mock_mod = _make_mock_extractor()
            original = _EXTRACTORS["teams_channels"]

            with patch.dict(_EXTRACTORS, {"teams_channels": (mock_mod, original[1], original[2])}):
                result = runner.invoke(main, args, catch_exceptions=False)

            mock_mod.run.assert_not_called()
            mock_state.save.assert_not_called()
            assert result.exit_code == 0

    def test_warns_on_unknown_extractor(self, runner, config_file, full_config):
        args = ["--config", config_file, "sync", "--once", "--extractors", "nonexistent"]
        with (
            _patch_cli("load_config") as mock_load,
            _patch_cli("make_cli_token_provider"),
            _patch_cli("create_storage"),
            _patch_cli("SyncState") as mock_state_cls,
            _patch_cli("GraphClient") as mock_gc,
            _patch_cli("structlog"),
            _patch_cli("log") as mock_log,
        ):
            _setup_mocks(mock_load, mock_state_cls, mock_gc, full_config)
            runner.invoke(main, args, catch_exceptions=False)
            mock_log.warning.assert_called_once_with("cli.unknown_extractor", name="nonexistent")

    def test_handles_extractor_exception(self, runner, config_file, full_config):
        args = ["--config", config_file, "sync", "--once", "--extractors", "email"]
        mock_mod = _make_mock_extractor(side_effect=RuntimeError("API down"))
        original = _EXTRACTORS["email"]

        with (
            _patch_cli("load_config") as mock_load,
            _patch_cli("make_cli_token_provider"),
            _patch_cli("create_storage"),
            _patch_cli("SyncState") as mock_state_cls,
            _patch_cli("GraphClient") as mock_gc,
            _patch_cli("structlog"),
            patch.dict(_EXTRACTORS, {"email": (mock_mod, original[1], original[2])}),
        ):
            mock_state, _ = _setup_mocks(mock_load, mock_state_cls, mock_gc, full_config)

            result = runner.invoke(main, args, catch_exceptions=False)

            assert "FAILED" in result.output
            assert "API down" in result.output
            mock_state.save.assert_not_called()

    def test_passes_converters_when_needed(self, runner, config_file, full_config):
        args = ["--config", config_file, "sync", "--once", "--extractors", "onedrive"]

        od_config = replace(full_config.extractors.onedrive, enabled=True)
        extractors = replace(full_config.extractors, onedrive=od_config)
        config = replace(full_config, extractors=extractors)

        mock_mod = _make_mock_extractor(return_value=({}, 5))
        original = _EXTRACTORS["onedrive"]

        with (
            _patch_cli("load_config") as mock_load,
            _patch_cli("make_cli_token_provider"),
            _patch_cli("create_storage"),
            _patch_cli("SyncState") as mock_state_cls,
            _patch_cli("GraphClient") as mock_gc,
            _patch_cli("structlog"),
            patch.dict(_EXTRACTORS, {"onedrive": (mock_mod, original[1], original[2])}),
        ):
            _setup_mocks(mock_load, mock_state_cls, mock_gc, config)

            result = runner.invoke(main, args, catch_exceptions=False)

            assert result.exit_code == 0
            mock_mod.run.assert_called_once()
            call_args = mock_mod.run.call_args[0]
            assert call_args[4] == config.converters

    def test_successful_run_saves_state_and_prints_count(self, runner, config_file, full_config):
        args = ["--config", config_file, "sync", "--once", "--extractors", "email"]
        mock_mod = _make_mock_extractor(return_value=({"delta": "abc"}, 7))
        original = _EXTRACTORS["email"]

        with (
            _patch_cli("load_config") as mock_load,
            _patch_cli("make_cli_token_provider"),
            _patch_cli("create_storage"),
            _patch_cli("SyncState") as mock_state_cls,
            _patch_cli("GraphClient") as mock_gc,
            _patch_cli("structlog"),
            patch.dict(_EXTRACTORS, {"email": (mock_mod, original[1], original[2])}),
        ):
            mock_state, _ = _setup_mocks(mock_load, mock_state_cls, mock_gc, full_config)

            result = runner.invoke(main, args, catch_exceptions=False)

            assert "email: 7 items written" in result.output
            mock_state.save.assert_called_once_with("email", {"delta": "abc"})


class TestRunContinuous:
    def test_respects_poll_interval_and_stops_on_interrupt(self, runner, config_file, full_config):
        """First cycle runs email; KeyboardInterrupt on sleep stops cleanly."""
        args = ["--config", config_file, "sync", "--continuous", "--extractors", "email"]
        mock_mod = _make_mock_extractor(return_value=({}, 3))
        original = _EXTRACTORS["email"]

        with (
            _patch_cli("load_config") as mock_load,
            _patch_cli("make_cli_token_provider"),
            _patch_cli("create_storage"),
            _patch_cli("SyncState") as mock_state_cls,
            _patch_cli("GraphClient") as mock_gc,
            _patch_cli("structlog"),
            patch.dict(_EXTRACTORS, {"email": (mock_mod, original[1], original[2])}),
            patch("m365_extract.cli.time") as mock_time,
        ):
            _setup_mocks(mock_load, mock_state_cls, mock_gc, full_config)
            mock_time.time.return_value = 1000.0
            mock_time.sleep.side_effect = KeyboardInterrupt

            result = runner.invoke(main, args)

            assert "Stopped" in result.output
            assert result.exit_code == 0
            mock_mod.run.assert_called_once()

    def test_skips_extractor_before_interval_elapses(self, runner, config_file, full_config):
        """Two cycles with same time: first runs (last_run=0), second skips (interval not elapsed)."""
        args = ["--config", config_file, "sync", "--continuous", "--extractors", "email"]
        mock_mod = _make_mock_extractor(return_value=({}, 2))
        original = _EXTRACTORS["email"]

        cycle = 0

        def sleep_side_effect(seconds):
            nonlocal cycle
            cycle += 1
            if cycle >= 2:
                raise KeyboardInterrupt

        with (
            _patch_cli("load_config") as mock_load,
            _patch_cli("make_cli_token_provider"),
            _patch_cli("create_storage"),
            _patch_cli("SyncState") as mock_state_cls,
            _patch_cli("GraphClient") as mock_gc,
            _patch_cli("structlog"),
            patch.dict(_EXTRACTORS, {"email": (mock_mod, original[1], original[2])}),
            patch("m365_extract.cli.time") as mock_time,
        ):
            _setup_mocks(mock_load, mock_state_cls, mock_gc, full_config)
            # time.time() returns 1000.0; first cycle: 1000-0=1000 >= 180 -> runs
            # last_run set to 1000; second cycle: 1000-1000=0 < 180 -> skip
            mock_time.time.return_value = 1000.0
            mock_time.sleep.side_effect = sleep_side_effect

            runner.invoke(main, args)

            assert mock_mod.run.call_count == 1


class TestAuthLogin:
    def test_echoes_token_length(self, runner, config_file, full_config):
        with (
            _patch_cli("load_config") as mock_load,
            _patch_cli("DeviceCodeAuth") as mock_auth_cls,
        ):
            mock_load.return_value = full_config
            mock_auth = MagicMock()
            mock_auth.login.return_value = "a" * 42
            mock_auth_cls.return_value = mock_auth

            result = runner.invoke(main, ["--config", config_file, "auth", "login"])

            assert result.exit_code == 0
            assert "42" in result.output
            assert "Authenticated successfully" in result.output
