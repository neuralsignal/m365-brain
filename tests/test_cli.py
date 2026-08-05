"""Tests for m365_brain.cli module."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import click.testing
import pytest


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
    return patch(f"m365_brain.cli.{target}")


def _patch_sync(target: str) -> patch:
    return patch(f"m365_brain.sync.{target}")


def _patch_dry_run(target: str) -> patch:
    return patch(f"m365_brain.dry_run.{target}")


class TestSyncCommand:
    def test_sync_requires_once_or_dry_run(self, runner, config_file):
        from m365_brain.cli import main

        result = runner.invoke(main, ["--config", config_file, "sync"])
        assert result.exit_code != 0
        assert "Specify --once or --dry-run" in result.output

    def test_sync_once_calls_run_extractors(self, runner, config_file, full_config):
        from m365_brain.cli import main

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

    def test_sync_once_with_extractor_filter(self, runner, config_file, full_config):
        from m365_brain.cli import main

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


class TestDryRun:
    def test_dry_run_success(self, runner, config_file, full_config):
        """--dry-run validates auth and probes enabled extractors."""
        from m365_brain.cli import main

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
        from m365_brain.cli import main
        from m365_brain.graph_client import GraphApiError

        mock_client = MagicMock()
        mock_client.get.side_effect = GraphApiError("HTTP 401 — token expired", 401)

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
        from m365_brain.cli import main
        from m365_brain.graph_client import GraphApiError

        call_count = 0

        def side_effect(path, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            # First call is /me — succeed
            if call_count == 1:
                return {"displayName": "Test", "userPrincipalName": "t@x.com", "value": []}
            # Second call is email probe — fail
            raise GraphApiError("HTTP 403 — Authorization_RequestDenied", 403)

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
        from m365_brain.cli import main

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

    def test_dry_run_unknown_extractor(self, runner, config_file, full_config):
        """--dry-run increments failed and logs warning for unknown extractor names."""
        from m365_brain.cli import main

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
                main, ["--config", config_file, "sync", "--dry-run", "--extractors", "nonexistent_extractor"]
            )

        assert result.exit_code == 1
        mock_log.warning.assert_any_call("cli.dry_run_probe_unknown", name="nonexistent_extractor")
        mock_log.info.assert_any_call("cli.dry_run_complete", passed=0, failed=1)

    def test_dry_run_no_probe_configured(self, runner, config_file, full_config):
        """--dry-run skips extractors with no probe URL configured."""
        from m365_brain.cli import main

        mock_client = MagicMock()
        mock_client.get.return_value = {"displayName": "Test", "userPrincipalName": "t@x.com", "value": []}

        with (
            _patch_cli("load_config") as mock_load,
            _patch_cli("make_cli_token_provider"),
            _patch_dry_run("GraphClient") as mock_gc,
            _patch_cli("configure_logging"),
            _patch_dry_run("log") as mock_log,
            _patch_dry_run("_dry_run_probe_path") as mock_probe,
        ):
            mock_load.return_value = full_config
            mock_gc.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_gc.return_value.__exit__ = MagicMock(return_value=False)
            mock_probe.return_value = None

            result = runner.invoke(main, ["--config", config_file, "sync", "--dry-run", "--extractors", "email"])

        assert result.exit_code == 0
        mock_log.info.assert_any_call("cli.dry_run_probe_skipped", name="email", reason="no probe configured")

    def test_requires_flag(self, runner, config_file):
        """sync without --once or --dry-run errors."""
        from m365_brain.cli import main

        result = runner.invoke(main, ["--config", config_file, "sync"])
        assert result.exit_code != 0
        assert "--once" in result.output or "--dry-run" in result.output


class TestAuthLogin:
    def test_echoes_token_length(self, runner, config_file, full_config):
        from m365_brain.cli import main

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
        from m365_brain.cli import main

        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("dummy: true")

        with _patch_cli("load_config") as mock_load:
            mock_load.return_value = full_config
            result = runner.invoke(main, ["--config", str(cfg_file), "auth", "status"])

        assert result.exit_code == 1
        assert "No cached token" in result.output

    def test_shows_account_info(self, runner, tmp_path, full_config):
        """auth status displays account username and tenant from cache."""
        from m365_brain.cli import main

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
        from m365_brain.cli import main

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
        from m365_brain.cli import main

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

    def test_no_access_tokens_branch(self, runner, tmp_path, full_config):
        """auth status echoes 'no access tokens cached' when Account exists but AccessToken is empty."""
        from m365_brain.cli import main

        cache_data = {
            "Account": {"acc-1": {"username": "user@test.com", "realm": "t-1"}},
            "AccessToken": {},
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
        assert "no access tokens cached" in result.output


class TestDotenvLoading:
    def test_loads_env_from_config_directory(self, runner, tmp_path):
        """main() loads .env from the config file's directory when one exists there."""
        from m365_brain.cli import main

        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("dummy: true")
        env_file = tmp_path / ".env"
        env_file.write_text("FOO=bar\n")

        with _patch_cli("load_dotenv") as mock_load_dotenv:
            # `sync` without --once or --dry-run errors fast, but the parent
            # group has already executed its dotenv-loading logic.
            runner.invoke(main, ["--config", str(cfg_file), "sync"])

        expected_env = (tmp_path / ".env").resolve()
        called_paths = [call.args[0] for call in mock_load_dotenv.call_args_list if call.args]
        assert expected_env in called_paths

    def test_skips_env_when_not_present(self, runner, tmp_path):
        """main() does not pass a config-dir .env path to load_dotenv when none exists."""
        from m365_brain.cli import main

        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("dummy: true")

        with _patch_cli("load_dotenv") as mock_load_dotenv:
            runner.invoke(main, ["--config", str(cfg_file), "sync"])

        unexpected_env = (tmp_path / ".env").resolve()
        called_paths = [call.args[0] for call in mock_load_dotenv.call_args_list if call.args]
        assert unexpected_env not in called_paths


class TestWorkerCommand:
    def test_requires_web_config(self, runner, config_file, full_config):
        """worker raises UsageError when config has no web: section."""
        from m365_brain.cli import main

        with (
            _patch_cli("load_config") as mock_load,
            _patch_cli("configure_logging"),
        ):
            mock_load.return_value = full_config  # web is None

            result = runner.invoke(main, ["--config", config_file, "worker"])

        assert result.exit_code != 0
        assert "worker requires a config with a 'web' section" in result.output

    def test_happy_path_invokes_worker_loop(self, runner, tmp_path, full_web_config):
        """worker constructs engine + token adapter and invokes worker_loop."""
        from m365_brain.cli import main

        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text("dummy: true")

        with (
            _patch_cli("load_config") as mock_load,
            _patch_cli("configure_logging"),
            patch("sqlmodel.create_engine") as mock_create_engine,
            patch("m365_admin.services.token_service.TokenService") as mock_ts_cls,
            patch("m365_admin.services.token_service.TokenServiceAdapter") as mock_adapter_cls,
            patch("m365_brain.worker.worker_loop") as mock_worker_loop,
        ):
            mock_load.return_value = full_web_config
            mock_engine = MagicMock()
            mock_create_engine.return_value = mock_engine
            mock_token_service = MagicMock()
            mock_ts_cls.return_value = mock_token_service
            mock_adapter = MagicMock()
            mock_adapter_cls.return_value = mock_adapter

            result = runner.invoke(main, ["--config", str(cfg_file), "worker"])

        assert result.exit_code == 0, result.output
        mock_create_engine.assert_called_once_with(full_web_config.web.db_url)
        mock_ts_cls.assert_called_once_with(fernet_key=full_web_config.web.fernet_key)
        mock_adapter_cls.assert_called_once_with(token_service=mock_token_service, engine=mock_engine)
        mock_worker_loop.assert_called_once()
        args = mock_worker_loop.call_args[0]
        assert args[0] is full_web_config
        assert args[1] is mock_engine
        assert args[2] is mock_adapter
        assert args[3] == str(Path(str(cfg_file)).resolve().parent / "state")
