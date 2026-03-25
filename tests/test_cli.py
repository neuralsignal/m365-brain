"""Tests for m365_extract.cli module."""

from __future__ import annotations

import json
import time
from unittest.mock import MagicMock, patch

import click.testing
import pytest

from m365_extract.sync import EXTRACTORS


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


def _patch_sync(target: str) -> patch:
    return patch(f"m365_extract.sync.{target}")


def _patch_continuous(target: str) -> patch:
    return patch(f"m365_extract.continuous.{target}")


def _patch_dry_run(target: str) -> patch:
    return patch(f"m365_extract.dry_run.{target}")


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
        _patch_cli("configure_logging"),
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
        from m365_extract.cli import main

        result = runner.invoke(main, ["--config", config_file, "sync"])
        assert result.exit_code != 0
        assert "Specify --once, --continuous, or --dry-run" in result.output

    def test_sync_once_calls_run_extractors(self, runner, config_file, full_config):
        from m365_extract.cli import main

        with (
            _patch_cli("load_config") as mock_load,
            _patch_cli("make_cli_token_provider"),
            _patch_cli("create_storage"),
            _patch_cli("SyncState"),
            _patch_cli("run_extractors") as mock_run,
            _patch_cli("configure_logging"),
        ):
            mock_load.return_value = full_config

            result = runner.invoke(main, ["--config", config_file, "sync", "--once"])

            assert result.exit_code == 0
            mock_run.assert_called_once()
            assert mock_run.call_args[0][0] is full_config

    def test_sync_continuous_calls_run_continuous(self, runner, config_file, full_config):
        from m365_extract.cli import main

        with (
            _patch_cli("load_config") as mock_load,
            _patch_cli("make_cli_token_provider"),
            _patch_cli("create_storage"),
            _patch_cli("SyncState"),
            _patch_cli("run_continuous") as mock_run,
            _patch_cli("configure_logging"),
        ):
            mock_load.return_value = full_config

            result = runner.invoke(main, ["--config", config_file, "sync", "--continuous"])

            assert result.exit_code == 0
            mock_run.assert_called_once()

    def test_sync_once_with_extractor_filter(self, runner, config_file, full_config):
        from m365_extract.cli import main

        with (
            _patch_cli("load_config") as mock_load,
            _patch_cli("make_cli_token_provider"),
            _patch_cli("create_storage"),
            _patch_cli("SyncState"),
            _patch_cli("run_extractors") as mock_run,
            _patch_cli("configure_logging"),
        ):
            mock_load.return_value = full_config

            result = runner.invoke(main, ["--config", config_file, "sync", "--once", "--extractors", "email,calendar"])

            assert result.exit_code == 0
            names_arg = mock_run.call_args[0][4]
            assert names_arg == ["email", "calendar"]


class TestRunContinuous:
    def test_respects_poll_interval_and_stops_on_interrupt(self, runner, config_file, full_config):
        """First cycle runs email; KeyboardInterrupt on sleep stops cleanly."""
        from m365_extract.cli import main

        args = ["--config", config_file, "sync", "--continuous", "--extractors", "email"]
        mock_mod = _make_mock_extractor(return_value=({}, 3))
        original = EXTRACTORS["email"]

        with (
            _patch_cli("load_config") as mock_load,
            _patch_cli("make_cli_token_provider"),
            _patch_cli("create_storage"),
            _patch_cli("SyncState") as mock_state_cls,
            _patch_continuous("GraphClient") as mock_gc,
            _patch_cli("configure_logging"),
            _patch_continuous("log"),
            patch.dict(EXTRACTORS, {"email": (mock_mod, original[1], original[2])}),
            patch("m365_extract.continuous.time") as mock_time,
        ):
            _setup_mocks(mock_load, mock_state_cls, mock_gc, full_config)
            mock_time.time.return_value = 1000.0
            mock_time.monotonic.return_value = 0.0
            mock_time.sleep.side_effect = KeyboardInterrupt

            result = runner.invoke(main, args)

            assert result.exit_code == 0
            mock_mod.run.assert_called_once()

    def test_skips_extractor_before_interval_elapses(self, runner, config_file, full_config):
        """Two cycles with same time: first runs (last_run=0), second skips (interval not elapsed)."""
        from m365_extract.cli import main

        args = ["--config", config_file, "sync", "--continuous", "--extractors", "email"]
        mock_mod = _make_mock_extractor(return_value=({}, 2))
        original = EXTRACTORS["email"]

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
            _patch_continuous("GraphClient") as mock_gc,
            _patch_cli("configure_logging"),
            _patch_continuous("log"),
            patch.dict(EXTRACTORS, {"email": (mock_mod, original[1], original[2])}),
            patch("m365_extract.continuous.time") as mock_time,
        ):
            _setup_mocks(mock_load, mock_state_cls, mock_gc, full_config)
            # time.time() returns 1000.0; first cycle: 1000-0=1000 >= 180 -> runs
            # last_run set to 1000; second cycle: 1000-1000=0 < 180 -> skip
            mock_time.time.return_value = 1000.0
            mock_time.monotonic.return_value = 0.0
            mock_time.sleep.side_effect = sleep_side_effect

            runner.invoke(main, args)

            assert mock_mod.run.call_count == 1

    def test_uses_continuous_poll_seconds_from_config(self, runner, config_file, full_config):
        """Sleep uses config.service.continuous_poll_seconds, not a hardcoded value."""
        from m365_extract.cli import main

        args = ["--config", config_file, "sync", "--continuous", "--extractors", "email"]
        mock_mod = _make_mock_extractor(return_value=({}, 1))
        original = EXTRACTORS["email"]

        with (
            _patch_cli("load_config") as mock_load,
            _patch_cli("make_cli_token_provider"),
            _patch_cli("create_storage"),
            _patch_cli("SyncState") as mock_state_cls,
            _patch_continuous("GraphClient") as mock_gc,
            _patch_cli("configure_logging"),
            _patch_continuous("log"),
            patch.dict(EXTRACTORS, {"email": (mock_mod, original[1], original[2])}),
            patch("m365_extract.continuous.time") as mock_time,
        ):
            _setup_mocks(mock_load, mock_state_cls, mock_gc, full_config)
            mock_time.time.return_value = 1000.0
            mock_time.monotonic.return_value = 0.0
            mock_time.sleep.side_effect = KeyboardInterrupt

            runner.invoke(main, args)

            mock_time.sleep.assert_called_with(full_config.service.continuous_poll_seconds)

    def test_auth_failure_recovers(self, runner, config_file, full_config):
        """GraphClient raises once, then succeeds — daemon continues, counter resets."""
        from m365_extract.cli import main
        from m365_extract.graph_client import GraphApiError

        args = ["--config", config_file, "sync", "--continuous", "--extractors", "email"]
        mock_mod = _make_mock_extractor(return_value=({}, 1))
        original = EXTRACTORS["email"]

        call_count = 0

        def gc_side_effect(*args_inner, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise GraphApiError("token expired")
            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            return mock_client

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
            _patch_continuous("GraphClient") as mock_gc,
            _patch_cli("configure_logging"),
            _patch_continuous("log"),
            patch.dict(EXTRACTORS, {"email": (mock_mod, original[1], original[2])}),
            patch("m365_extract.continuous.time") as mock_time,
        ):
            mock_load.return_value = full_config
            mock_state = MagicMock()
            mock_state.load.return_value = {}
            mock_state_cls.return_value = mock_state
            mock_gc.side_effect = gc_side_effect
            mock_time.time.return_value = 1000.0
            mock_time.monotonic.return_value = 0.0
            mock_time.sleep.side_effect = sleep_side_effect

            result = runner.invoke(main, args)

            assert result.exit_code == 0
            # Second cycle succeeded, so the extractor ran
            assert mock_mod.run.call_count == 1

    def test_exits_after_max_consecutive_auth_failures(self, runner, config_file, full_config):
        """GraphClient raises N consecutive times — SystemExit(1)."""
        from m365_extract.cli import main
        from m365_extract.graph_client import GraphApiError

        args = ["--config", config_file, "sync", "--continuous", "--extractors", "email"]
        original = EXTRACTORS["email"]
        mock_mod = _make_mock_extractor(return_value=({}, 0))

        with (
            _patch_cli("load_config") as mock_load,
            _patch_cli("make_cli_token_provider"),
            _patch_cli("create_storage"),
            _patch_cli("SyncState") as mock_state_cls,
            _patch_continuous("GraphClient") as mock_gc,
            _patch_cli("configure_logging"),
            _patch_continuous("log"),
            patch.dict(EXTRACTORS, {"email": (mock_mod, original[1], original[2])}),
            patch("m365_extract.continuous.time") as mock_time,
        ):
            mock_load.return_value = full_config
            mock_state = MagicMock()
            mock_state.load.return_value = {}
            mock_state_cls.return_value = mock_state
            # GraphClient constructor always raises
            mock_gc.side_effect = GraphApiError("auth failed")
            mock_time.time.return_value = 1000.0
            mock_time.monotonic.return_value = 0.0
            mock_time.sleep.return_value = None

            result = runner.invoke(main, args)

            assert result.exit_code == 1

    def test_heartbeat_logged_each_cycle(self, runner, config_file, full_config):
        """Verify cli.continuous_heartbeat event emitted each cycle."""
        from m365_extract.cli import main

        args = ["--config", config_file, "sync", "--continuous", "--extractors", "email"]
        mock_mod = _make_mock_extractor(return_value=({}, 1))
        original = EXTRACTORS["email"]

        with (
            _patch_cli("load_config") as mock_load,
            _patch_cli("make_cli_token_provider"),
            _patch_cli("create_storage"),
            _patch_cli("SyncState") as mock_state_cls,
            _patch_continuous("GraphClient") as mock_gc,
            _patch_cli("configure_logging"),
            _patch_continuous("log") as mock_log,
            patch.dict(EXTRACTORS, {"email": (mock_mod, original[1], original[2])}),
            patch("m365_extract.continuous.time") as mock_time,
        ):
            _setup_mocks(mock_load, mock_state_cls, mock_gc, full_config)
            mock_time.time.return_value = 1000.0
            mock_time.monotonic.return_value = 0.0
            mock_time.sleep.side_effect = KeyboardInterrupt

            runner.invoke(main, args)

            heartbeat_calls = [c for c in mock_log.info.call_args_list if c[0][0] == "cli.continuous_heartbeat"]
            assert len(heartbeat_calls) >= 1
            assert heartbeat_calls[0][1]["loop"] == 1


class TestDryRun:
    def test_dry_run_success(self, runner, config_file, full_config):
        """--dry-run validates auth and probes enabled extractors."""
        from m365_extract.cli import main

        mock_client = MagicMock()
        mock_client.get.return_value = {
            "displayName": "Test User",
            "userPrincipalName": "test@x.com",
            "value": [{"id": "1"}],
        }

        with (
            _patch_cli("load_config") as mock_load,
            _patch_cli("make_cli_token_provider"),
            _patch_dry_run("GraphClient") as mock_gc,
            _patch_cli("configure_logging"),
            _patch_dry_run("log") as mock_log,
        ):
            mock_load.return_value = full_config
            mock_gc.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_gc.return_value.__exit__ = MagicMock(return_value=False)

            result = runner.invoke(main, ["--config", config_file, "sync", "--dry-run"])

        assert result.exit_code == 0
        mock_log.info.assert_any_call("cli.dry_run_auth_ok", user="Test User", upn="test@x.com")
        mock_log.info.assert_any_call("cli.dry_run_complete", passed=3, failed=0)

    def test_dry_run_auth_failure(self, runner, config_file, full_config):
        """--dry-run exits 1 when /me call fails."""
        from m365_extract.cli import main
        from m365_extract.graph_client import GraphApiError

        mock_client = MagicMock()
        mock_client.get.side_effect = GraphApiError("HTTP 401 — token expired")

        with (
            _patch_cli("load_config") as mock_load,
            _patch_cli("make_cli_token_provider"),
            _patch_dry_run("GraphClient") as mock_gc,
            _patch_cli("configure_logging"),
            _patch_dry_run("log") as mock_log,
        ):
            mock_load.return_value = full_config
            mock_gc.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_gc.return_value.__exit__ = MagicMock(return_value=False)

            result = runner.invoke(main, ["--config", config_file, "sync", "--dry-run"])

        assert result.exit_code == 1
        mock_log.error.assert_any_call("cli.dry_run_auth_failed", error="HTTP 401 — token expired")

    def test_dry_run_extractor_probe_failure(self, runner, config_file, full_config):
        """--dry-run reports per-extractor failures and exits 1."""
        from m365_extract.cli import main
        from m365_extract.graph_client import GraphApiError

        call_count = 0

        def side_effect(path, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            # First call is /me — succeed
            if call_count == 1:
                return {"displayName": "Test", "userPrincipalName": "t@x.com", "value": []}
            # Second call is email probe — fail
            raise GraphApiError("HTTP 403 — Authorization_RequestDenied")

        mock_client = MagicMock()
        mock_client.get.side_effect = side_effect

        with (
            _patch_cli("load_config") as mock_load,
            _patch_cli("make_cli_token_provider"),
            _patch_dry_run("GraphClient") as mock_gc,
            _patch_cli("configure_logging"),
            _patch_dry_run("log") as mock_log,
        ):
            mock_load.return_value = full_config
            mock_gc.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_gc.return_value.__exit__ = MagicMock(return_value=False)

            result = runner.invoke(main, ["--config", config_file, "sync", "--dry-run", "--extractors", "email"])

        assert result.exit_code == 1
        mock_log.error.assert_any_call(
            "cli.dry_run_probe_failed", name="email", error="HTTP 403 — Authorization_RequestDenied"
        )

    def test_dry_run_skips_disabled_extractors(self, runner, config_file, full_config):
        """--dry-run skips disabled extractors."""
        from m365_extract.cli import main

        mock_client = MagicMock()
        mock_client.get.return_value = {"displayName": "Test", "userPrincipalName": "t@x.com", "value": []}

        with (
            _patch_cli("load_config") as mock_load,
            _patch_cli("make_cli_token_provider"),
            _patch_dry_run("GraphClient") as mock_gc,
            _patch_cli("configure_logging"),
            _patch_dry_run("log") as mock_log,
        ):
            mock_load.return_value = full_config
            mock_gc.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_gc.return_value.__exit__ = MagicMock(return_value=False)

            result = runner.invoke(
                main, ["--config", config_file, "sync", "--dry-run", "--extractors", "teams_channels"]
            )

        assert result.exit_code == 0
        mock_log.info.assert_any_call("cli.dry_run_probe_skipped", name="teams_channels", reason="disabled")

    def test_requires_flag(self, runner, config_file):
        """sync without --once, --continuous, or --dry-run errors."""
        from m365_extract.cli import main

        result = runner.invoke(main, ["--config", config_file, "sync"])
        assert result.exit_code != 0
        assert "--once" in result.output or "--dry-run" in result.output


class TestAuthLogin:
    def test_echoes_token_length(self, runner, config_file, full_config):
        from m365_extract.cli import main

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


class TestAuthStatus:
    def test_no_cache_file(self, runner, tmp_path, full_config):
        """auth status exits 1 when no token cache exists."""
        from m365_extract.cli import main

        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("dummy: true")

        with _patch_cli("load_config") as mock_load:
            mock_load.return_value = full_config
            result = runner.invoke(main, ["--config", str(cfg_file), "auth", "status"])

        assert result.exit_code == 1
        assert "No cached token" in result.output

    def test_shows_account_info(self, runner, tmp_path, full_config):
        """auth status displays account username and tenant from cache."""
        from m365_extract.cli import main

        cache_data = {
            "Account": {
                "acc-1": {
                    "username": "matthias@sanoptis.com",
                    "realm": "tenant-id-abc",
                }
            },
            "AccessToken": {
                "tok-1": {
                    "target": "Mail.Read Calendars.Read User.Read",
                    "expires_on": str(int(time.time()) + 3600),
                }
            },
        }
        cache_path = tmp_path / "token_cache.json"
        cache_path.write_text(json.dumps(cache_data))

        patched_config = full_config.model_copy(
            update={"auth": full_config.auth.model_copy(update={"token_cache_path": str(cache_path)})},
        )
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("dummy: true")

        with _patch_cli("load_config") as mock_load:
            mock_load.return_value = patched_config
            result = runner.invoke(main, ["--config", str(cfg_file), "auth", "status"])

        assert result.exit_code == 0
        assert "matthias@sanoptis.com" in result.output
        assert "tenant-id-abc" in result.output
        assert "valid" in result.output
        assert "Calendars.Read" in result.output

    def test_shows_expired_token(self, runner, tmp_path, full_config):
        """auth status reports expired token."""
        from m365_extract.cli import main

        cache_data = {
            "Account": {"acc-1": {"username": "user@test.com", "realm": "t-1"}},
            "AccessToken": {
                "tok-1": {
                    "target": "User.Read",
                    "expires_on": str(int(time.time()) - 600),
                }
            },
        }
        cache_path = tmp_path / "token_cache.json"
        cache_path.write_text(json.dumps(cache_data))

        patched_config = full_config.model_copy(
            update={"auth": full_config.auth.model_copy(update={"token_cache_path": str(cache_path)})},
        )
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("dummy: true")

        with _patch_cli("load_config") as mock_load:
            mock_load.return_value = patched_config
            result = runner.invoke(main, ["--config", str(cfg_file), "auth", "status"])

        assert result.exit_code == 0
        assert "expired" in result.output

    def test_empty_accounts(self, runner, tmp_path, full_config):
        """auth status exits 1 when cache has no accounts."""
        from m365_extract.cli import main

        cache_data = {"Account": {}, "AccessToken": {}}
        cache_path = tmp_path / "token_cache.json"
        cache_path.write_text(json.dumps(cache_data))

        patched_config = full_config.model_copy(
            update={"auth": full_config.auth.model_copy(update={"token_cache_path": str(cache_path)})},
        )
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("dummy: true")

        with _patch_cli("load_config") as mock_load:
            mock_load.return_value = patched_config
            result = runner.invoke(main, ["--config", str(cfg_file), "auth", "status"])

        assert result.exit_code == 1
        assert "no accounts" in result.output


@pytest.mark.admin
class TestDaemonCommand:
    """Tests for the CLI `daemon` command.

    Uses full_web_config (has web: section with db_url: "sqlite://").
    Mocks run_daemon_cycle and time.sleep — lets real engine, SQLModel, and
    TokenService/TokenServiceAdapter run.
    """

    def test_daemon_requires_web_config(self, runner, config_file, full_config):
        """Config without web: section raises UsageError."""
        from m365_extract.cli import main

        with _patch_cli("load_config") as mock_load, _patch_cli("configure_logging"):
            mock_load.return_value = full_config  # web=None

            result = runner.invoke(main, ["--config", config_file, "daemon"])

        assert result.exit_code != 0
        assert "web" in result.output.lower()

    def test_daemon_calls_run_daemon_cycle(self, runner, config_file, full_web_config):
        """Daemon calls run_daemon_cycle at least once before KeyboardInterrupt stops it."""
        from m365_extract.cli import main

        with (
            _patch_cli("load_config") as mock_load,
            _patch_cli("configure_logging"),
            patch("alembic.command.upgrade"),
            patch("m365_extract.daemon.run_daemon_cycle") as mock_cycle,
            patch("m365_extract.cli.time") as mock_time,
        ):
            mock_load.return_value = full_web_config
            mock_cycle.return_value = []
            mock_time.sleep.side_effect = KeyboardInterrupt

            result = runner.invoke(main, ["--config", config_file, "daemon"])

        assert result.exit_code == 0
        mock_cycle.assert_called_once()

    def test_daemon_stops_on_keyboard_interrupt(self, runner, config_file, full_web_config):
        """Exit code 0 on KeyboardInterrupt."""
        from m365_extract.cli import main

        with (
            _patch_cli("load_config") as mock_load,
            _patch_cli("configure_logging"),
            patch("alembic.command.upgrade"),
            patch("m365_extract.daemon.run_daemon_cycle") as mock_cycle,
            patch("m365_extract.cli.time") as mock_time,
        ):
            mock_load.return_value = full_web_config
            mock_cycle.return_value = []
            mock_time.sleep.side_effect = KeyboardInterrupt

            result = runner.invoke(main, ["--config", config_file, "daemon"])

        assert result.exit_code == 0

    def test_daemon_poll_interval_override(self, runner, config_file, full_web_config):
        """--poll-interval 60 passes 60 to time.sleep."""
        from m365_extract.cli import main

        with (
            _patch_cli("load_config") as mock_load,
            _patch_cli("configure_logging"),
            patch("alembic.command.upgrade"),
            patch("m365_extract.daemon.run_daemon_cycle") as mock_cycle,
            patch("m365_extract.cli.time") as mock_time,
        ):
            mock_load.return_value = full_web_config
            mock_cycle.return_value = []
            mock_time.sleep.side_effect = KeyboardInterrupt

            result = runner.invoke(main, ["--config", config_file, "daemon", "--poll-interval", "60"])

        assert result.exit_code == 0
        mock_time.sleep.assert_called_with(60)

    def test_daemon_uses_config_poll_interval(self, runner, config_file, full_web_config):
        """Without --poll-interval, uses config.service.continuous_poll_seconds."""
        from m365_extract.cli import main

        with (
            _patch_cli("load_config") as mock_load,
            _patch_cli("configure_logging"),
            patch("alembic.command.upgrade"),
            patch("m365_extract.daemon.run_daemon_cycle") as mock_cycle,
            patch("m365_extract.cli.time") as mock_time,
        ):
            mock_load.return_value = full_web_config
            mock_cycle.return_value = []
            mock_time.sleep.side_effect = KeyboardInterrupt

            result = runner.invoke(main, ["--config", config_file, "daemon"])

        assert result.exit_code == 0
        mock_time.sleep.assert_called_with(full_web_config.service.continuous_poll_seconds)

    def test_daemon_state_dir_from_config_path(self, runner, tmp_path, full_web_config):
        """state_dir is derived from config file's parent directory."""
        from m365_extract.cli import main

        cfg_file = tmp_path / "config.web.yaml"
        cfg_file.write_text("dummy: true")
        expected_state_dir = str(tmp_path.resolve() / "state")

        with (
            _patch_cli("load_config") as mock_load,
            _patch_cli("configure_logging"),
            patch("alembic.command.upgrade"),
            patch("m365_extract.daemon.run_daemon_cycle") as mock_cycle,
            patch("m365_extract.cli.time") as mock_time,
        ):
            mock_load.return_value = full_web_config
            mock_cycle.return_value = []
            mock_time.sleep.side_effect = KeyboardInterrupt

            result = runner.invoke(main, ["--config", str(cfg_file), "daemon"])

        assert result.exit_code == 0
        # run_daemon_cycle receives state_dir as 4th positional arg
        call_args = mock_cycle.call_args
        assert call_args[0][3] == expected_state_dir
