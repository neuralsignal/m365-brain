"""AC-3: drive the whole loop from the CLI alone, in a scratch directory.

The stage gate. Everything below runs in a `tmp_path` that contains nothing
this repo put there -- no config, no vault, no state, no fixtures on disk -- and
every step is a `m365-brain` invocation. If a step needed something that is not
in the installed package, the package is not self-contained, and that is the
finding rather than a test to loosen.

**What is stubbed and why.** Two things, both named here so nobody has to
reverse-engineer the boundary:

* *The token.* `auth login` is a device-code flow: it prints a code and waits
  for a human at a browser. There is no honest way to automate it, so the
  token provider is replaced with a constant and the interactive step is
  covered by `scripts/independence_check.sh`, which stops and prints the exact
  command. The stub replaces one function -- the same seam the CLI itself uses.
* *Graph.* Recorded responses via `pytest_httpx`. The transport, the delta
  handling, the markdown rendering, the vault layout, the index, the manifest,
  the hook and the outbox are all real.

What is deliberately **not** stubbed: the config file (written by `init` and
read back off disk by every subsequent invocation), the vault directory
structure, the SQLite index, the state store, the manifest store, the hook
import, and the intent file. Those are the parts that would break if the
package were not self-contained.
"""

from __future__ import annotations

import importlib.util
import json
import re
import sys
import uuid
from pathlib import Path

import pytest
from click.testing import CliRunner
from pytest_httpx import HTTPXMock

from m365_brain.cli import main
from tests.conftest import load_fixture

pytestmark = pytest.mark.usefixtures("scratch")

ENV = {
    "MSAL_CLIENT_ID": "00000000-0000-0000-0000-00000000c101",
    "MSAL_TENANT_ID": "00000000-0000-0000-0000-0000000071d0",
    "M365_MAIL_CLIENT_ID": "00000000-0000-0000-0000-00000000ma11",
    "M365_FILES_CLIENT_ID": "00000000-0000-0000-0000-000000000f11",
    "M365_CHAT_CLIENT_ID": "00000000-0000-0000-0000-0000000000ch",
    "M365_OWN_EMAIL": "operator@example.invalid",
}

HOOK_MODULE = """
import json, pathlib

def on_cycle(manifest):
    pathlib.Path("hook_saw.json").write_text(json.dumps({
        "cycle_id": manifest.cycle_id,
        "ok": manifest.ok,
        "added": manifest.paths(kind="added", extractor=None),
    }))
"""

INTENT = """---
uuid: {uuid}
schema_version: 1
created_at: 2026-08-05T09:00:00Z
created_by: independence-check
payload:
  kind: email.draft
  mailbox: me
  to: ["someone@example.com"]
  cc: null
  bcc: null
  subject: independence check
  attachments: null
  inline_images: null
  include_signature: false
  revises_message_id: null
---
Written by the CLI gate. Delete it.
"""
"""Every field, because every field is required.

An intent is a typed markdown file with `extra="forbid"` and no defaults, so a
half-filled one is rejected with a receipt rather than dispatched with silently
invented values. `docs/dev/quickstart.md` carries the same document; this one
is the copy that is executed."""


@pytest.fixture()
def scratch(tmp_path, monkeypatch):
    """A directory with no trace of this repo, and it is the working directory."""
    for key, value in ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr(
        "m365_brain.commands._context.make_cli_token_provider", lambda auth_config: lambda: "gate-token"
    )
    monkeypatch.setattr("m365_brain.commands.outbox.AuthProfiles.provider", lambda self, name: lambda: "gate-token")
    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))
    return tmp_path


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


def cli(runner: CliRunner, *args: str):
    """One `m365-brain` invocation. Fails loudly with the output on non-zero."""
    result = runner.invoke(main, list(args))
    assert result.exit_code == 0, f"m365-brain {' '.join(args)} -> {result.exit_code}\n{result.output}"
    return result


@pytest.fixture()
def graph(httpx_mock: HTTPXMock) -> HTTPXMock:
    for pattern, payload in (
        (r".*/me/mailFolders/[^/]+/messages/delta.*", load_fixture("email_response.json")),
        (r".*/me/calendarView.*", load_fixture("calendar_response.json")),
        (r".*/me/chats\?.*", {"value": []}),
        (r".*/me/contacts/delta.*", {"value": [], "@odata.deltaLink": "https://delta?token=x"}),
    ):
        httpx_mock.add_response(url=re.compile(pattern), json=payload, is_reusable=True, is_optional=True)
    httpx_mock.add_response(
        url=re.compile(r".*/me/messages.*"),
        method="POST",
        json={"id": "AAMkAGdraft1", "conversationId": "conv-1"},
        is_reusable=True,
        is_optional=True,
    )
    return httpx_mock


def test_the_whole_loop_from_the_cli_alone(runner, scratch, graph):
    config = scratch / "m365-brain.yaml"

    # 1. config and vault from nothing --------------------------------------
    cli(runner, "init", str(config), "--vault", str(scratch / "vault"))
    assert config.is_file()
    _use_offline_embeddings(config)
    for area in ("inbox", "annotations", "outbox", "_meta"):
        assert (scratch / "vault" / area).is_dir()

    cli(runner, "--config", str(config), "config", "validate")
    shown = json.loads(cli(runner, "--config", str(config), "config", "show", "--json").stdout)
    assert shown["vault"]["root"] == str(scratch / "vault")

    inbox = cli(runner, "--config", str(config), "vault", "path", "inbox", "--extractor", "email").stdout.strip()
    assert inbox == f"{scratch / 'vault'}/inbox/emails"

    # 2. authenticate (stubbed here; see the module docstring) ---------------
    status = runner.invoke(main, ["--config", str(config), "auth", "status", "--json"])
    assert json.loads(status.stdout)["profiles"], "auth status must report the configured profiles"

    # 3. wire a hook, then run a cycle ---------------------------------------
    (scratch / "gate_hook.py").write_text(HOOK_MODULE, encoding="utf-8")
    config.write_text(
        config.read_text(encoding="utf-8").replace("post_cycle: []", 'post_cycle: ["gate_hook:on_cycle"]'),
        encoding="utf-8",
    )

    manifest = json.loads(
        cli(runner, "--config", str(config), "run", "--once", "--only", "email,calendar,index", "--json").stdout
    )
    assert manifest["ok"] is True
    assert {entry["name"] for entry in manifest["extractors"]} == {"email", "calendar"}
    assert manifest["index"] is not None

    latest = scratch / "vault" / "_meta" / "manifests" / "latest.json"
    assert latest.is_file()
    assert json.loads(latest.read_text(encoding="utf-8"))["cycle_id"] == manifest["cycle_id"]

    # 4. the hook received the manifest --------------------------------------
    seen = json.loads((scratch / "hook_saw.json").read_text(encoding="utf-8"))
    assert seen["cycle_id"] == manifest["cycle_id"]
    assert seen["added"] == manifest["extractors"][0]["changes"][0]["path"] or seen["added"]

    # 5. search what the cycle produced --------------------------------------
    results = json.loads(cli(runner, "--config", str(config), "index", "search", "budget", "--json").stdout)
    assert [hit["title"] for hit in results["results"]] == ["Q1 Budget Review"]

    hybrid = cli(runner, "--config", str(config), "index", "search", "standup", "--search-type", "hybrid", "--json")
    assert json.loads(hybrid.stdout)["results"]

    # 6. write an intent by hand, then push it -------------------------------
    intent_uuid = str(uuid.uuid4())
    draft_dir = scratch / "vault" / "outbox" / "email.draft"
    draft_dir.mkdir(parents=True, exist_ok=True)
    (draft_dir / f"{intent_uuid}.md").write_text(INTENT.format(uuid=intent_uuid), encoding="utf-8")

    listed = json.loads(cli(runner, "--config", str(config), "outbox", "list", "--json").stdout)
    assert [row["uuid"] for row in listed["intents"] if row["status"] == "pending"] == [intent_uuid]

    counts = json.loads(cli(runner, "--config", str(config), "outbox", "push", "--json").stdout)
    assert counts["dispatched"] == 1, counts

    after = json.loads(cli(runner, "--config", str(config), "outbox", "list", "--json").stdout)
    assert any(row["uuid"] == intent_uuid and row["status"] == "dispatched" for row in after["intents"])

    # 7. status reports the cycle and the units ------------------------------
    reported = json.loads(runner.invoke(main, ["--config", str(config), "status", "--json"]).stdout)
    assert reported["last_cycle"]["cycle_id"] == manifest["cycle_id"]
    assert reported["units"]["email"]["last_success_at"]


def test_nothing_the_cli_produced_names_a_consuming_workspace(runner, scratch, graph):
    """The repo's own vocabulary check, pointed at what the CLI just wrote.

    The checker is imported rather than re-implemented: a second copy of the
    wordlist is a second thing to keep in step, and the copy that drifts is
    always the one nobody is looking at.
    """
    cli(runner, "init", str(scratch / "m365-brain.yaml"), "--vault", str(scratch / "vault"))
    _use_offline_embeddings(scratch / "m365-brain.yaml")
    cli(runner, "--config", str(scratch / "m365-brain.yaml"), "run", "--once", "--only", "calendar")

    assert _workspace_checker().scan(scratch) == []


def _workspace_checker():
    script = Path(__file__).resolve().parents[2] / "scripts" / "check_no_workspace.py"
    spec = importlib.util.spec_from_file_location("check_no_workspace", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_console_script_is_installed():
    """`m365-brain` on PATH is the entry point every other step assumes."""
    from importlib.metadata import entry_points

    scripts = {entry.name: entry.value for entry in entry_points(group="console_scripts")}
    assert scripts.get("m365-brain") == "m365_brain.cli:main"


def test_the_config_template_ships_inside_the_wheel():
    """`init` reads it via importlib.resources, so a source checkout is not needed."""
    from importlib import resources

    template = resources.files("m365_brain.templates").joinpath("m365-brain.yaml")
    assert template.is_file()
    assert "vault:" in template.read_text(encoding="utf-8")


def test_the_package_imports_without_the_repo_on_the_path():
    """No test helper, no fixture directory, no repo-relative path at import."""
    import m365_brain.cli

    assert Path(m365_brain.cli.__file__).parent.name == "m365_brain"
    assert sys.modules["m365_brain.cli"].main.name == "main"


def _use_offline_embeddings(config: Path) -> None:
    """Swap the shipped embedding backend for the in-process fake.

    The gate proves the loop is driveable, and the loop is indifferent to which
    provider computes the vectors. Leaving `fastembed` in would make the gate
    download a model -- turning a self-containment check into a network test,
    which is the opposite of what it is for. Both values are config, and
    switching them here is the point: no code changes.
    """
    text = config.read_text(encoding="utf-8")
    text = text.replace('provider: "fastembed"', 'provider: "hash"')
    text = text.replace('store: "sqlite_vec"', 'store: "memory"')
    config.write_text(text, encoding="utf-8")
