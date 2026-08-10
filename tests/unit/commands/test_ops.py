"""CLI-level tests for `ops resolve-links`, `ops tiers`, and `ops triage`.

Each command is a thin wrapper: load config, open workspace, call a library
function, emit.  The library functions are tested exhaustively in
`tests/unit/test_ops.py`; these tests cover the wiring the CLI adds -- option
parsing, section gating, JSON and tabular output.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yaml
from click.testing import CliRunner
from hypothesis import given
from hypothesis import strategies as st

from m365_brain.cli import main
from m365_brain.commands._context import EXIT_CONFIG, EXIT_OK
from m365_brain.commands.ops import _fields
from m365_brain.config.ops import TriageFieldsConfig

FIELDS_KEYS = list(TriageFieldsConfig.model_fields)
LATIN = st.text(alphabet=st.characters(min_codepoint=97, max_codepoint=122), min_size=1, max_size=10)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _ops_section() -> dict:
    return {
        "link_resolution": {"unresolved_prefix": "contact-", "target_types": ["person"]},
        "tiers": {
            "lookback_days": 90,
            "ladder": [
                {"name": "close", "min_per_month": 4.0, "stale_after_days": 14},
                {"name": "rest", "min_per_month": 0.0, "stale_after_days": None},
            ],
            "interaction_sources": [
                {
                    "entity_type": "email",
                    "party_from": {"observation": "from", "relation": None},
                    "timestamp": {"observation": "received_at"},
                    "exclude_future": True,
                }
            ],
        },
        "triage": {
            "own_email": "owner@example.com",
            "inbox_folder": "Inbox",
            "sent_folders": ["SentItems"],
            "forward_prefixes": ["fw:"],
            "fields": {
                "entity_type": "email",
                "folder": "folder",
                "conversation_id": "conversation_id",
                "message_id": "message_id",
                "sender": "sender",
                "recipients": "to",
                "timestamp": "date",
            },
        },
    }


def _outboxes_section() -> dict:
    return {
        "attachment_root": "./attachments",
        "forbidden_send_scopes": ["Mail.Send"],
        "definitions": {"email.draft": {"authority": "draft_only", "auth_profile": "mail"}},
        "email": {"signature": {"html_path": None, "logo_path": None, "logo_content_id": "logo"}},
        "reconcile": {"quote_markers": ["^From:"]},
    }


def _write_config(base_config, tmp_path, *, ops: bool, outboxes: bool) -> Path:
    """Serialize a Config to YAML, optionally adding `ops:` and `outboxes:`."""
    payload = base_config.model_dump(mode="json")
    if ops:
        payload["ops"] = _ops_section()
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


def _entity_md(title: str, permalink: str, entity_type: str, observations: dict[str, str]) -> str:
    """A minimal markdown file with the given frontmatter observations."""
    lines = [
        "---",
        f"title: {title}",
        f"permalink: {permalink}",
        f"type: {entity_type}",
        "tags: []",
    ]
    for category, value in observations.items():
        lines.append(f"{category}: {value}")
    lines.append("---")
    lines.append(f"# {title}")
    lines.append("")
    return "\n".join(lines)


def _run(runner: CliRunner, config_file: Path, *args: str):
    return runner.invoke(main, ["--config", str(config_file), *args])


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def offline_tokens(monkeypatch):
    monkeypatch.setattr(
        "m365_brain.commands._context.make_cli_token_provider", lambda auth_config: lambda: "test-token"
    )


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


# ---------------------------------------------------------------------------
# missing `ops:` section
# ---------------------------------------------------------------------------


class TestMissingOpsSection:
    def test_resolve_links_without_ops_is_config_error(self, runner, runtime_config, tmp_path):
        config_file = _write_config(runtime_config, tmp_path, ops=False, outboxes=False)
        result = _run(runner, config_file, "ops", "resolve-links")
        assert result.exit_code == EXIT_CONFIG
        assert "ops" in result.output.lower()

    def test_tiers_without_ops_is_config_error(self, runner, runtime_config, tmp_path):
        config_file = _write_config(runtime_config, tmp_path, ops=False, outboxes=False)
        result = _run(runner, config_file, "ops", "tiers")
        assert result.exit_code == EXIT_CONFIG
        assert "ops" in result.output.lower()


# ---------------------------------------------------------------------------
# ops resolve-links
# ---------------------------------------------------------------------------


class TestResolveLinksCommand:
    @pytest.fixture()
    def populated_config(self, runtime_config, tmp_path):
        config_file = _write_config(runtime_config, tmp_path, ops=True, outboxes=False)
        vault = tmp_path / "vault"
        vault.mkdir(exist_ok=True)

        (vault / "person-anna.md").write_text(
            _entity_md("Anna Meier", "person-anna", "person", {"email": "anna@example.com"}),
            encoding="utf-8",
        )
        (vault / "note-one.md").write_text(
            _entity_md("Note one", "note-one", "note", {}).rstrip()
            + "\n\nLinks to [[contact-anna-meier]] and [[contact-nobody]].\n",
            encoding="utf-8",
        )
        _run(CliRunner(), config_file, "index", "sync")
        return config_file

    def test_tabular_output(self, runner, populated_config):
        result = _run(runner, populated_config, "ops", "resolve-links")
        assert result.exit_code == EXIT_OK, result.output
        assert "contact-anna-meier" in result.output

    def test_json_output(self, runner, populated_config):
        result = _run(runner, populated_config, "ops", "resolve-links", "--json")
        assert result.exit_code == EXIT_OK, result.output
        payload = json.loads(result.stdout)
        assert "resolutions" in payload
        links = {r["link"] for r in payload["resolutions"]}
        assert "contact-anna-meier" in links


# ---------------------------------------------------------------------------
# ops tiers
# ---------------------------------------------------------------------------


class TestTiersCommand:
    @pytest.fixture()
    def populated_config(self, runtime_config, tmp_path):
        config_file = _write_config(runtime_config, tmp_path, ops=True, outboxes=False)
        vault = tmp_path / "vault"
        vault.mkdir(exist_ok=True)

        for day in (1, 5, 9, 13, 17, 21):
            (vault / f"email-{day}.md").write_text(
                _entity_md(
                    f"Message {day}",
                    f"email-{day}",
                    "email",
                    {"from": "anna@example.com", "received_at": f"2026-07-{day:02d}T09:00:00Z"},
                ),
                encoding="utf-8",
            )
        _run(CliRunner(), config_file, "index", "sync")
        return config_file

    def test_tabular_output(self, runner, populated_config):
        result = _run(runner, populated_config, "ops", "tiers")
        assert result.exit_code == EXIT_OK, result.output
        assert "anna@example.com" in result.output

    def test_json_output(self, runner, populated_config):
        result = _run(runner, populated_config, "ops", "tiers", "--json")
        assert result.exit_code == EXIT_OK, result.output
        payload = json.loads(result.stdout)
        assert "assignments" in payload
        parties = {a["party"] for a in payload["assignments"]}
        assert "anna@example.com" in parties


# ---------------------------------------------------------------------------
# ops triage
# ---------------------------------------------------------------------------


class TestTriageCommand:
    @pytest.fixture()
    def populated_config(self, runtime_config, tmp_path):
        config_file = _write_config(runtime_config, tmp_path, ops=True, outboxes=True)
        vault = tmp_path / "vault"
        vault.mkdir(exist_ok=True)

        outbox = vault / "outbox"
        outbox.mkdir(exist_ok=True)
        (outbox / "email.draft").mkdir(parents=True, exist_ok=True)

        recent = (datetime.now(UTC) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        (vault / "msg-open.md").write_text(
            _entity_md(
                "Question",
                "msg-open",
                "email",
                {
                    "folder": "Inbox",
                    "conversation_id": "conv-1",
                    "message_id": "msg-open",
                    "sender": "alice@example.com",
                    "to": "owner@example.com",
                    "date": recent,
                },
            ),
            encoding="utf-8",
        )
        _run(CliRunner(), config_file, "index", "sync")
        return config_file

    def test_tabular_output(self, runner, populated_config):
        result = _run(runner, populated_config, "ops", "triage", "--timeframe", "7d")
        assert result.exit_code == EXIT_OK, result.output
        assert "Question" in result.output

    def test_json_output(self, runner, populated_config):
        result = _run(runner, populated_config, "ops", "triage", "--timeframe", "7d", "--json")
        assert result.exit_code == EXIT_OK, result.output
        payload = json.loads(result.stdout)
        assert "messages" in payload
        assert any(m["permalink"] == "msg-open" for m in payload["messages"])

    def test_missing_outboxes_is_config_error(self, runner, runtime_config, tmp_path):
        config_file = _write_config(runtime_config, tmp_path, ops=True, outboxes=False)
        result = _run(runner, config_file, "ops", "triage", "--timeframe", "7d")
        assert result.exit_code == EXIT_CONFIG
        assert "outboxes" in result.output.lower()

    def test_missing_vault_is_config_error(self, runner, runtime_config, tmp_path):
        config = runtime_config.model_copy(update={"vault": None})
        payload = config.model_dump(mode="json")
        payload["ops"] = _ops_section()
        payload["outboxes"] = _outboxes_section()
        payload["auth"]["profiles"] = {
            "mail": {
                "client_id": "mail-id",
                "tenant_id": "tenant",
                "scopes": ["Mail.ReadWrite"],
                "token_cache_path": str(tmp_path / "mail.json"),
                "client_secret": None,
            }
        }
        path = tmp_path / "m365-brain.yaml"
        path.write_text(yaml.safe_dump(payload, sort_keys=True), encoding="utf-8")
        result = _run(runner, path, "ops", "triage", "--timeframe", "7d")
        assert result.exit_code == EXIT_CONFIG
        assert "vault" in result.output.lower()


# ---------------------------------------------------------------------------
# _fields: property-based tests
# ---------------------------------------------------------------------------


FIELDS = TriageFieldsConfig(
    entity_type="email",
    folder="folder",
    conversation_id="conversation",
    message_id="graph_id",
    sender="sender",
    recipients="to",
    timestamp="date",
)


class TestFieldsProperty:
    @given(st.fixed_dictionaries({key: LATIN for key in FIELDS_KEYS}))
    def test_all_overrides_replace_every_field(self, overrides):
        result = _fields(FIELDS, overrides)
        for key in FIELDS_KEYS:
            assert getattr(result, key) == overrides[key]

    @given(st.fixed_dictionaries({key: st.none() for key in FIELDS_KEYS}))
    def test_all_none_overrides_preserve_config(self, overrides):
        assert _fields(FIELDS, overrides) == FIELDS

    @given(st.fixed_dictionaries({key: st.one_of(LATIN, st.none()) for key in FIELDS_KEYS}))
    def test_result_is_always_a_valid_model(self, overrides):
        result = _fields(FIELDS, overrides)
        assert isinstance(result, TriageFieldsConfig)

    @given(st.fixed_dictionaries({key: LATIN for key in FIELDS_KEYS}))
    def test_idempotent_when_no_overrides(self, overrides):
        first = _fields(FIELDS, overrides)
        second = _fields(first, dict.fromkeys(FIELDS_KEYS))
        assert first == second
