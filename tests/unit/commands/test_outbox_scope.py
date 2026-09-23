"""CLI outbox selection must also scope receipts in the shared archive."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from click.testing import CliRunner

from m365_brain.cli import main
from m365_brain.commands.outbox import _store
from m365_brain.config import Config
from m365_brain.config.outbox import OutboxDefinitionConfig
from m365_brain.vault.dispatch import DispatchReceipt
from tests.unit.commands.test_outbox import (
    _clients_yielding,
    _config_with_outboxes,
    _MailboxStub,
    _write_config,
)
from tests.unit.outbox.conftest import DRAFT_PAYLOAD, intent_markdown

EMAIL_KINDS = ("email.draft", "email.reply", "email.forward")


@pytest.fixture()
def archived_email_kinds(runtime_config: Config, tmp_path: Path) -> Path:
    config = _config_with_outboxes(runtime_config, tmp_path)
    config = config.model_copy(
        update={
            "outboxes": config.outboxes.model_copy(
                update={
                    "definitions": {
                        kind: OutboxDefinitionConfig(authority="draft_only", auth_profile="mail")
                        for kind in EMAIL_KINDS
                    },
                }
            ),
        }
    )
    store = _store(config, EMAIL_KINDS)
    for kind in EMAIL_KINDS:
        uuid = kind.replace(".", "-")
        payload = {**DRAFT_PAYLOAD, "kind": kind}
        if kind != "email.draft":
            del payload["bcc"], payload["subject"]
            payload["in_reply_to"] = "MSG-original"
        if kind == "email.reply":
            del payload["to"]
            payload["reply_all"] = False
        store.put(kind, uuid, intent_markdown(uuid, payload, "Synthetic draft body."))
        store.claim(kind, uuid)
        store.archive(
            uuid,
            DispatchReceipt(
                uuid=uuid,
                kind=kind,
                outcome="dispatched",
                dispatched_at=datetime(2026, 9, 8, tzinfo=UTC),
                graph_message_id=f"MSG-{uuid}",
                reason=None,
                detail=None,
            ),
        )
    return _write_config(config, tmp_path)


@pytest.mark.parametrize("only", [*EMAIL_KINDS, None])
@pytest.mark.parametrize("verb", ["list", "reconcile"])
def test_selected_outbox_limits_archived_receipts_and_graph_reads(
    archived_email_kinds: Path,
    monkeypatch: pytest.MonkeyPatch,
    only: str | None,
    verb: str,
) -> None:
    client = _MailboxStub({}, {"isDraft": True})
    monkeypatch.setattr("m365_brain.commands.outbox._clients", _clients_yielding(client))
    arguments = ["--config", str(archived_email_kinds), "outbox", verb, "--json"]
    if only is not None:
        arguments.extend(["--outbox", only])

    result = CliRunner().invoke(main, arguments)

    assert result.exit_code == 0, result.exception or result.output
    expected = {kind.replace(".", "-") for kind in (EMAIL_KINDS if only is None else (only,))}
    rows = json.loads(result.stdout)["intents" if verb == "list" else "outcomes"]
    assert {row["uuid"] for row in rows} == expected
    if verb == "reconcile":
        assert set(client.asked) == {f"MSG-{uuid}" for uuid in expected}
        assert len(client.asked) == len(expected)
