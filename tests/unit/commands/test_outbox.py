"""`outbox list --outbox bogus`, `outbox push` failure exit, `outbox reconcile`."""

from __future__ import annotations

import json
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from click.testing import CliRunner

from m365_brain.cli import main
from m365_brain.commands._context import EXIT_CONFIG, EXIT_FAILURE, EXIT_OK
from m365_brain.config import AuthProfileConfig, OutboxesConfig
from m365_brain.config.outbox import (
    EmailOutboxConfig,
    EmailSignatureConfig,
    OutboxDefinitionConfig,
    ReconcileConfig,
)
from m365_brain.config.runtime import M365Config, UploadConfig
from m365_brain.outbox.reconcile import ReconcileOutcome
from m365_brain.outbox.runner import PushCounts


@pytest.fixture(autouse=True)
def offline_tokens(monkeypatch):
    monkeypatch.setattr(
        "m365_brain.commands._context.make_cli_token_provider",
        lambda auth_config: lambda: "test-token",
    )


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


def _outboxes(tmp_path: Path) -> OutboxesConfig:
    return OutboxesConfig(
        attachment_root=str(tmp_path / "assets"),
        forbidden_send_scopes=["Mail.Send"],
        definitions={
            "email.draft": OutboxDefinitionConfig(authority="draft_only", auth_profile="mail"),
        },
        email=EmailOutboxConfig(
            signature=EmailSignatureConfig(html_path=None, logo_path=None, logo_content_id="brand_logo"),
        ),
        reconcile=ReconcileConfig(quote_markers=["> On "]),
    )


def _config_with_outboxes(runtime_config, tmp_path):
    outboxes = _outboxes(tmp_path)
    profiles = {
        "mail": AuthProfileConfig(
            client_id="mail-client-id",
            tenant_id="test-tenant-id",
            scopes=["Mail.Read"],
            token_cache_path=str(tmp_path / "mail_cache.json"),
            client_secret=None,
        ),
    }
    m365 = M365Config(
        upload=UploadConfig(
            inline_attachment_max_bytes=3_000_000,
            simple_upload_max_bytes=4_000_000,
            chunk_bytes=327_680,
        ),
    )
    return runtime_config.model_copy(
        update={
            "outboxes": outboxes,
            "m365": m365,
            "auth": runtime_config.auth.model_copy(update={"profiles": profiles}),
        },
    )


def _write_config(config, tmp_path) -> Path:
    (tmp_path / "vault").mkdir(exist_ok=True)
    path = tmp_path / "m365-brain.yaml"
    path.write_text(yaml.safe_dump(config.model_dump(mode="json"), sort_keys=True), encoding="utf-8")
    return path


def _run(runner, config_file, *args):
    return runner.invoke(main, ["--config", str(config_file), *args])


# ── _names: unknown outbox ──────────────────────────────────────────────────


class TestNamesUnknownOutbox:
    def test_unknown_outbox_raises_config_error(self, runner, runtime_config, tmp_path):
        config = _config_with_outboxes(runtime_config, tmp_path)
        config_file = _write_config(config, tmp_path)

        result = _run(runner, config_file, "outbox", "list", "--outbox", "bogus")

        assert result.exit_code == EXIT_CONFIG
        assert "bogus" in result.output
        assert "email.draft" in result.output

    def test_valid_outbox_name_filters(self, runner, runtime_config, tmp_path):
        config = _config_with_outboxes(runtime_config, tmp_path)
        config_file = _write_config(config, tmp_path)

        result = _run(runner, config_file, "outbox", "list", "--outbox", "email.draft", "--json")

        assert result.exit_code == EXIT_OK, result.output


# ── push: exits 1 on failures ───────────────────────────────────────────────


@contextmanager
def _fake_clients(_config):
    yield {"mail": "fake-client"}


class TestPushFailureExit:
    def test_push_exits_failure_when_counts_failed(self, runner, runtime_config, tmp_path):
        config = _config_with_outboxes(runtime_config, tmp_path)
        config_file = _write_config(config, tmp_path)

        with (
            patch("m365_brain.commands.outbox._clients", _fake_clients),
            patch("m365_brain.commands.outbox.build_registry"),
            patch("m365_brain.commands.outbox.push_pass", return_value=PushCounts(failed=1)),
        ):
            result = _run(runner, config_file, "outbox", "push")

        assert result.exit_code == EXIT_FAILURE

    def test_push_exits_ok_when_no_failures(self, runner, runtime_config, tmp_path):
        config = _config_with_outboxes(runtime_config, tmp_path)
        config_file = _write_config(config, tmp_path)

        with (
            patch("m365_brain.commands.outbox._clients", _fake_clients),
            patch("m365_brain.commands.outbox.build_registry"),
            patch("m365_brain.commands.outbox.push_pass", return_value=PushCounts()),
        ):
            result = _run(runner, config_file, "outbox", "push")

        assert result.exit_code == EXIT_OK


# ── reconcile ────────────────────────────────────────────────────────────────


class TestReconcileHappyPath:
    def test_reconcile_empty_outcomes_json(self, runner, runtime_config, tmp_path):
        config = _config_with_outboxes(runtime_config, tmp_path)
        config_file = _write_config(config, tmp_path)

        with (
            patch("m365_brain.commands.outbox._clients", _fake_clients),
            patch("m365_brain.commands.outbox.reconcile_pass", return_value=[]),
        ):
            result = _run(runner, config_file, "outbox", "reconcile", "--json")

        assert result.exit_code == EXIT_OK, result.output
        payload = json.loads(result.stdout)
        assert payload == {"outcomes": []}

    def test_reconcile_empty_outcomes_human(self, runner, runtime_config, tmp_path):
        config = _config_with_outboxes(runtime_config, tmp_path)
        config_file = _write_config(config, tmp_path)

        with (
            patch("m365_brain.commands.outbox._clients", _fake_clients),
            patch("m365_brain.commands.outbox.reconcile_pass", return_value=[]),
        ):
            result = _run(runner, config_file, "outbox", "reconcile")

        assert result.exit_code == EXIT_OK
        assert result.output.strip() == ""


class TestReconcileWithOutcomes:
    def test_reconcile_outputs_verdict_and_uuid(self, runner, runtime_config, tmp_path):
        config = _config_with_outboxes(runtime_config, tmp_path)
        config_file = _write_config(config, tmp_path)

        outcome = ReconcileOutcome(
            uuid="abc-123",
            verdict="sent",
            graph_message_id="msg-001",
            conversation_id="conv-001",
            sent_at="2026-01-01T00:00:00Z",
            sent_body_html="<p>hello</p>",
            original_body="hello",
        )

        with (
            patch("m365_brain.commands.outbox._clients", _fake_clients),
            patch("m365_brain.commands.outbox.reconcile_pass", return_value=[outcome]),
        ):
            result = _run(runner, config_file, "outbox", "reconcile", "--json")

        assert result.exit_code == EXIT_OK, result.output
        payload = json.loads(result.stdout)
        assert len(payload["outcomes"]) == 1
        assert payload["outcomes"][0]["uuid"] == "abc-123"
        assert payload["outcomes"][0]["verdict"] == "sent"

    def test_reconcile_human_output_includes_verdict_and_uuid(self, runner, runtime_config, tmp_path):
        config = _config_with_outboxes(runtime_config, tmp_path)
        config_file = _write_config(config, tmp_path)

        outcome = ReconcileOutcome(
            uuid="def-456",
            verdict="amended",
            graph_message_id="msg-002",
            conversation_id="conv-002",
            sent_at="2026-02-01T00:00:00Z",
            sent_body_html="<p>changed</p>",
            original_body="original",
        )

        with (
            patch("m365_brain.commands.outbox._clients", _fake_clients),
            patch("m365_brain.commands.outbox.reconcile_pass", return_value=[outcome]),
        ):
            result = _run(runner, config_file, "outbox", "reconcile")

        assert result.exit_code == EXIT_OK
        assert "amended" in result.output
        assert "def-456" in result.output
