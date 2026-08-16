"""CLI-level tests for ``vault path outbox`` and ``_require_known_outbox``.

The other ``vault path`` areas are already covered; these tests add the
``outbox`` branch (lines 40-44) and the ``ConfigError`` path in
``_require_known_outbox`` (lines 61-63).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from m365_brain.cli import main
from m365_brain.commands._context import EXIT_CONFIG, EXIT_OK, EXIT_USAGE


def _outboxes_section() -> dict:
    return {
        "attachment_root": "./attachments",
        "forbidden_send_scopes": ["Mail.Send"],
        "definitions": {"email.draft": {"authority": "draft_only", "auth_profile": "mail"}},
        "email": {"signature": {"html_path": None, "logo_path": None, "logo_content_id": "logo"}},
        "reconcile": {"quote_markers": ["^From:"]},
    }


def _write_config(base_config, tmp_path, *, outboxes: bool) -> Path:
    payload = base_config.model_dump(mode="json")
    if outboxes:
        payload["outboxes"] = _outboxes_section()
        payload.setdefault("auth", {}).setdefault("profiles", {})
        payload["auth"]["profiles"] = {
            "mail": {
                "client_id": "mail-id",
                "tenant_id": "tenant",
                "scopes": ["Mail.ReadWrite"],
                "token_cache_path": str(tmp_path / "mail.json"),
                "client_secret": None,
            }
        }
    (tmp_path / "vault").mkdir(exist_ok=True)
    path = tmp_path / "m365-brain.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=True), encoding="utf-8")
    return path


def _run(runner: CliRunner, config_file: Path, *args: str):
    return runner.invoke(main, ["--config", str(config_file), *args])


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


class TestVaultPathOutbox:
    def test_happy_path(self, runner, runtime_config, tmp_path):
        config_file = _write_config(runtime_config, tmp_path, outboxes=True)
        result = _run(runner, config_file, "vault", "path", "outbox", "--outbox", "email.draft")
        assert result.exit_code == EXIT_OK, result.output
        assert "outbox" in result.output
        assert "email.draft" in result.output

    def test_missing_outbox_flag(self, runner, runtime_config, tmp_path):
        config_file = _write_config(runtime_config, tmp_path, outboxes=True)
        result = _run(runner, config_file, "vault", "path", "outbox")
        assert result.exit_code == EXIT_USAGE

    def test_unknown_outbox_name(self, runner, runtime_config, tmp_path):
        config_file = _write_config(runtime_config, tmp_path, outboxes=True)
        result = _run(runner, config_file, "vault", "path", "outbox", "--outbox", "bogus")
        assert result.exit_code == EXIT_CONFIG
        assert "bogus" in result.output
