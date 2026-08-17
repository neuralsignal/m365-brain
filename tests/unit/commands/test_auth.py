"""CLI-level tests for `auth login`.

The login command body (lines 58–65) is the uncovered path: it requires a real
MSAL device-code flow, so the tests mock `AuthProfiles.login` and
`AuthProfiles.status` to exercise the wiring and output formatting without
network calls.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from m365_brain.cli import main
from m365_brain.commands._context import EXIT_CONFIG
from m365_brain.m365.auth.profiles import ProfileStatus


def _write_config(base_config, tmp_path) -> Path:
    payload = base_config.model_dump(mode="json")
    (tmp_path / "vault").mkdir(exist_ok=True)
    path = tmp_path / "m365-brain.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=True), encoding="utf-8")
    return path


def _run(runner: CliRunner, config_file: Path, *args: str):
    return runner.invoke(main, ["--config", str(config_file), *args])


@pytest.fixture(autouse=True)
def offline_tokens(monkeypatch):
    monkeypatch.setattr(
        "m365_brain.commands._context.make_cli_token_provider", lambda auth_config: lambda: "test-token"
    )


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


class TestLoginUnknownProfile:
    def test_exits_with_config_error(self, runner, runtime_config, tmp_path):
        config_file = _write_config(runtime_config, tmp_path)
        result = _run(runner, config_file, "auth", "login", "--profile", "nonexistent")
        assert result.exit_code == EXIT_CONFIG
        assert "nonexistent" in result.output


class TestLoginSuccessOutput:
    def test_echo_lines_after_login(self, runner, runtime_config, tmp_path, monkeypatch):
        config_file = _write_config(runtime_config, tmp_path)
        stub = ProfileStatus(
            name="default",
            state="authenticated",
            accounts=("user@example.com",),
            scopes=("Mail.Read", "User.Read"),
            token_cache_path="/tmp/cache.json",
        )
        monkeypatch.setattr("m365_brain.m365.auth.profiles.AuthProfiles.login", lambda self, name: None)
        monkeypatch.setattr("m365_brain.m365.auth.profiles.AuthProfiles.status", lambda self, name: stub)

        result = _run(runner, config_file, "auth", "login", "--profile", "default")
        assert result.exit_code == 0, result.output
        assert "default: authenticated" in result.output
        assert "accounts: user@example.com" in result.output
        assert "Mail.Read" in result.output
        assert "User.Read" in result.output
