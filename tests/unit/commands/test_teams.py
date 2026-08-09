"""`teams post` -- the untested half: envelope construction, storage write, emit."""

from __future__ import annotations

import json
import uuid as uuid_module
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner

from m365_brain.cli import main
from m365_brain.commands._context import EXIT_CONFIG, EXIT_OK
from m365_brain.config import AuthProfileConfig, OutboxesConfig
from m365_brain.config.outbox import (
    EmailOutboxConfig,
    EmailSignatureConfig,
    OutboxDefinitionConfig,
    ReconcileConfig,
)

VALID_CHANNEL_URL = (
    "https://teams.microsoft.com/l/channel/19%3Aabc123%40thread.tacv2/"
    "General?groupId=11111111-2222-3333-4444-555555555555&tenantId=t"
)
EXPECTED_TEAM_ID = "11111111-2222-3333-4444-555555555555"
EXPECTED_CHANNEL_ID = "19:abc123@thread.tacv2"


@pytest.fixture(autouse=True)
def offline_tokens(monkeypatch):
    monkeypatch.setattr(
        "m365_brain.commands._context.make_cli_token_provider", lambda auth_config: lambda: "test-token"
    )


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


def _outboxes(tmp_path: Path) -> OutboxesConfig:
    return OutboxesConfig(
        attachment_root=str(tmp_path / "assets"),
        forbidden_send_scopes=["Mail.Send"],
        definitions={
            "teams.post_message": OutboxDefinitionConfig(authority="auto_send", auth_profile="teams"),
        },
        email=EmailOutboxConfig(
            signature=EmailSignatureConfig(html_path=None, logo_path=None, logo_content_id="brand_logo")
        ),
        reconcile=ReconcileConfig(quote_markers=["> On "]),
    )


def _config_with_outboxes(runtime_config, tmp_path):
    outboxes = _outboxes(tmp_path)
    profiles = {
        "teams": AuthProfileConfig(
            client_id="teams-client-id",
            tenant_id="test-tenant-id",
            scopes=["ChannelMessage.Send"],
            token_cache_path=str(tmp_path / "teams_cache.json"),
            client_secret=None,
        ),
    }
    return runtime_config.model_copy(
        update={
            "outboxes": outboxes,
            "auth": runtime_config.auth.model_copy(update={"profiles": profiles}),
        }
    )


def _write_config(config, tmp_path) -> Path:
    (tmp_path / "vault").mkdir(exist_ok=True)
    path = tmp_path / "m365-brain.yaml"
    path.write_text(yaml.safe_dump(config.model_dump(mode="json"), sort_keys=True), encoding="utf-8")
    return path


def _body_file(tmp_path: Path) -> Path:
    body = tmp_path / "body.md"
    body.write_text("Hello from the test.\n", encoding="utf-8")
    return body


def _run(runner, config_file, *args):
    return runner.invoke(main, ["--config", str(config_file), *args])


class TestTeamsPostSuccess:
    def test_writes_intent_and_emits_path(self, runner, runtime_config, tmp_path):
        config = _config_with_outboxes(runtime_config, tmp_path)
        config_file = _write_config(config, tmp_path)
        body = _body_file(tmp_path)

        result = _run(
            runner,
            config_file,
            "teams",
            "post",
            "--channel-url",
            VALID_CHANNEL_URL,
            "--body-file",
            str(body),
            "--created-by",
            "tester",
        )
        assert result.exit_code == EXIT_OK, result.output
        assert EXPECTED_TEAM_ID in result.output or "outbox" in result.output

    def test_written_file_is_valid_intent_envelope(self, runner, runtime_config, tmp_path):
        config = _config_with_outboxes(runtime_config, tmp_path)
        config_file = _write_config(config, tmp_path)
        body = _body_file(tmp_path)

        result = _run(
            runner,
            config_file,
            "teams",
            "post",
            "--channel-url",
            VALID_CHANNEL_URL,
            "--body-file",
            str(body),
            "--created-by",
            "tester",
            "--json",
        )
        assert result.exit_code == EXIT_OK, result.output
        payload = json.loads(result.stdout)
        intent_uuid = payload["uuid"]

        vault_root = tmp_path / "vault"
        intent_path = vault_root / "outbox" / "teams.post_message" / f"{intent_uuid}.md"
        assert intent_path.exists(), f"intent file missing at {intent_path}"

        text = intent_path.read_text(encoding="utf-8")
        assert text.startswith("---\n")
        front = yaml.safe_load(text.split("---\n")[1])

        assert front["schema_version"] == 1
        assert front["payload"]["kind"] == "teams.post_message"
        uuid_module.UUID(front["uuid"])
        assert front["payload"]["team_id"] == EXPECTED_TEAM_ID
        assert front["payload"]["channel_id"] == EXPECTED_CHANNEL_ID
        assert front["created_by"] == "tester"
        assert "Hello from the test." in text


class TestTeamsPostJson:
    def test_json_output_includes_ids(self, runner, runtime_config, tmp_path):
        config = _config_with_outboxes(runtime_config, tmp_path)
        config_file = _write_config(config, tmp_path)
        body = _body_file(tmp_path)

        result = _run(
            runner,
            config_file,
            "teams",
            "post",
            "--channel-url",
            VALID_CHANNEL_URL,
            "--body-file",
            str(body),
            "--created-by",
            "tester",
            "--json",
        )
        assert result.exit_code == EXIT_OK, result.output
        payload = json.loads(result.stdout)
        assert payload["team_id"] == EXPECTED_TEAM_ID
        assert payload["channel_id"] == EXPECTED_CHANNEL_ID
        uuid_module.UUID(payload["uuid"])
        assert "path" in payload


class TestTeamsPostErrors:
    def test_missing_outbox_definition_is_config_error(self, runner, runtime_config, tmp_path):
        outboxes = OutboxesConfig(
            attachment_root=str(tmp_path / "assets"),
            forbidden_send_scopes=["Mail.Send"],
            definitions={
                "email.draft": OutboxDefinitionConfig(authority="draft_only", auth_profile="mail"),
            },
            email=EmailOutboxConfig(
                signature=EmailSignatureConfig(html_path=None, logo_path=None, logo_content_id="brand_logo")
            ),
            reconcile=ReconcileConfig(quote_markers=["> On "]),
        )
        profiles = {
            "mail": AuthProfileConfig(
                client_id="mail-client-id",
                tenant_id="test-tenant-id",
                scopes=["Mail.Read"],
                token_cache_path=str(tmp_path / "mail_cache.json"),
                client_secret=None,
            ),
        }
        config = runtime_config.model_copy(
            update={
                "outboxes": outboxes,
                "auth": runtime_config.auth.model_copy(update={"profiles": profiles}),
            }
        )
        config_file = _write_config(config, tmp_path)
        body = _body_file(tmp_path)

        result = _run(
            runner,
            config_file,
            "teams",
            "post",
            "--channel-url",
            VALID_CHANNEL_URL,
            "--body-file",
            str(body),
            "--created-by",
            "tester",
        )
        assert result.exit_code == EXIT_CONFIG
        assert "teams.post_message" in result.output

    def test_bad_channel_url_missing_group_id(self, runner, runtime_config, tmp_path):
        config = _config_with_outboxes(runtime_config, tmp_path)
        config_file = _write_config(config, tmp_path)
        body = _body_file(tmp_path)

        result = _run(
            runner,
            config_file,
            "teams",
            "post",
            "--channel-url",
            "https://teams.microsoft.com/l/channel/19%3Aabc%40thread.tacv2/General",
            "--body-file",
            str(body),
            "--created-by",
            "tester",
        )
        assert result.exit_code == EXIT_CONFIG
        assert "groupId" in result.output

    def test_bad_channel_url_missing_channel_segment(self, runner, runtime_config, tmp_path):
        config = _config_with_outboxes(runtime_config, tmp_path)
        config_file = _write_config(config, tmp_path)
        body = _body_file(tmp_path)

        result = _run(
            runner,
            config_file,
            "teams",
            "post",
            "--channel-url",
            "https://teams.microsoft.com/some/path?groupId=abc-123",
            "--body-file",
            str(body),
            "--created-by",
            "tester",
        )
        assert result.exit_code == EXIT_CONFIG
        assert "channel id" in result.output
