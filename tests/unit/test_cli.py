"""Every verb: help renders, options are enforced, and the exit codes hold.

The exit-code table is the contract a supervisor scripts against, so it is
asserted per code rather than as "non-zero". `--json` output is parsed rather
than substring-matched, because "it parses" is the property a caller depends on.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import structlog
import yaml
from click.testing import CliRunner
from pytest_httpx import HTTPXMock

from m365_brain.cli import main
from m365_brain.commands._context import (
    EXIT_AUTH,
    EXIT_CONFIG,
    EXIT_FAILURE,
    EXIT_NOT_FOUND,
    EXIT_OK,
    EXIT_USAGE,
)
from m365_brain.commands.teams import parse_channel_url
from m365_brain.config import ConfigError
from m365_brain.logging_config import _stderr_logger
from tests.conftest import load_fixture

SECRET = "s3cr3t-value-do-not-print"

ALL_VERBS = [
    ("init",),
    ("run",),
    ("extract",),
    ("status",),
    ("auth",),
    ("auth", "login"),
    ("auth", "status"),
    ("config",),
    ("config", "validate"),
    ("config", "show"),
    ("index",),
    ("index", "sync"),
    ("index", "rebuild"),
    ("index", "search"),
    ("index", "context"),
    ("index", "recent"),
    ("index", "paths"),
    ("index", "catalog"),
    ("index", "catalog", "list"),
    ("index", "catalog", "search"),
    ("index", "catalog", "resolve"),
    ("index", "catalog", "extract"),
    ("index", "catalog", "read"),
    ("outbox",),
    ("outbox", "list"),
    ("outbox", "push"),
    ("outbox", "reconcile"),
    ("files",),
    ("files", "pull"),
    ("files", "push"),
    ("teams",),
    ("teams", "post"),
    ("vault",),
    ("vault", "path"),
]

# Verbs that need only a config file and a vault on disk -- no Graph, no index.
CONFIG_ONLY = [
    ("config", "validate"),
    ("config", "show"),
    ("vault", "path", "inbox", "--extractor", "email"),
    ("vault", "path", "meta"),
    ("vault", "path", "state"),
    ("vault", "path", "manifests"),
    ("vault", "path", "annotations"),
    ("status",),
]


@pytest.fixture(autouse=True)
def offline_tokens(monkeypatch):
    """MSAL performs authority discovery over the network on first token use.

    Patched at the one seam the CLI builds a provider through, so these tests
    exercise the wiring without a tenant. `pytest_httpx` cannot cover it --
    MSAL uses `requests`, not `httpx`.
    """
    monkeypatch.setattr(
        "m365_brain.commands._context.make_cli_token_provider", lambda auth_config: lambda: "test-token"
    )


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture()
def config_file(runtime_config, tmp_path):
    """`runtime_config` serialised to YAML, exactly as the loader will read it."""
    (tmp_path / "vault").mkdir(exist_ok=True)
    path = tmp_path / "m365-brain.yaml"
    path.write_text(yaml.safe_dump(runtime_config.model_dump(mode="json"), sort_keys=True), encoding="utf-8")
    return path


def _run(runner: CliRunner, config_file, *args: str):
    return runner.invoke(main, ["--config", str(config_file), *args])


class TestHelp:
    @pytest.mark.parametrize("verb", ALL_VERBS, ids=lambda v: " ".join(v))
    def test_help_renders(self, runner, verb):
        result = runner.invoke(main, [*verb, "--help"])
        assert result.exit_code == EXIT_OK, result.output
        assert "Usage:" in result.output

    def test_the_root_lists_every_group(self, runner):
        output = runner.invoke(main, ["--help"]).output
        for name in ("auth", "config", "index", "outbox", "files", "teams", "vault", "run", "extract", "status"):
            assert re.search(rf"^\s+{name}\b", output, re.M), name


class TestExitCodes:
    def test_success_is_zero(self, runner, config_file):
        assert _run(runner, config_file, "config", "validate").exit_code == EXIT_OK

    def test_a_bad_command_line_is_two(self, runner, config_file):
        assert _run(runner, config_file, "index", "sync", "--nonsense").exit_code == EXIT_USAGE

    def test_a_missing_required_option_is_two(self, runner, config_file):
        assert _run(runner, config_file, "auth", "login").exit_code == EXIT_USAGE

    def test_a_missing_config_is_three(self, runner):
        result = runner.invoke(main, ["config", "validate"])
        assert result.exit_code == EXIT_CONFIG
        assert "--config is required" in result.output

    def test_a_config_file_that_does_not_exist_is_three(self, runner, tmp_path):
        result = runner.invoke(main, ["--config", str(tmp_path / "nope.yaml"), "config", "show"])
        assert result.exit_code == EXIT_CONFIG

    def test_invalid_yaml_is_three(self, runner, tmp_path):
        broken = tmp_path / "broken.yaml"
        broken.write_text("auth: {client_id: 1}\n", encoding="utf-8")
        assert runner.invoke(main, ["--config", str(broken), "config", "validate"]).exit_code == EXIT_CONFIG

    def test_an_unresolvable_hook_is_three(self, runtime_config, tmp_path, runner):
        from m365_brain.config import HooksConfig

        config = runtime_config.model_copy(
            update={"hooks": HooksConfig(post_cycle=["no_such_package:on_cycle"], post_reconcile=[])}
        )
        path = tmp_path / "hooked.yaml"
        path.write_text(yaml.safe_dump(config.model_dump(mode="json")), encoding="utf-8")
        result = runner.invoke(main, ["--config", str(path), "config", "validate"])
        assert result.exit_code == EXIT_CONFIG
        assert "cannot import module" in result.output

    def test_an_unknown_unit_is_three(self, runner, config_file):
        result = _run(runner, config_file, "run", "--once", "--only", "nope")
        assert result.exit_code == EXIT_CONFIG
        assert "unknown or disabled" in result.output

    def test_an_unknown_index_root_is_three(self, runner, config_file):
        result = _run(runner, config_file, "index", "sync", "--root", "nope")
        assert result.exit_code == EXIT_CONFIG

    def test_an_unknown_vault_area_is_two(self, runner, config_file):
        assert _run(runner, config_file, "vault", "path", "nowhere").exit_code == EXIT_USAGE

    def test_an_inbox_path_without_an_extractor_is_two(self, runner, config_file):
        """A missing companion flag is a command-line mistake, not a bad config.

        `index context` already exits 2 for the same class (give ENTITY or
        --permalink, not neither). This used to exit 3, so one command answered
        two variants of "you typed it wrong" with two different codes.
        """
        result = _run(runner, config_file, "vault", "path", "inbox")
        assert result.exit_code == EXIT_USAGE
        assert "needs --extractor" in result.output

    def test_an_unauthenticated_profile_is_four(self, runner, config_file):
        result = _run(runner, config_file, "auth", "status", "--json")
        assert result.exit_code == EXIT_AUTH
        assert json.loads(result.stdout)["profiles"][0]["valid"] is False

    def test_an_unknown_auth_profile_is_three(self, runner, config_file):
        assert _run(runner, config_file, "auth", "status", "--profile", "nope").exit_code == EXIT_CONFIG

    def test_a_failing_cycle_is_one(self, runner, config_file, httpx_mock: HTTPXMock):
        httpx_mock.add_response(url=re.compile(r".*"), status_code=500, is_reusable=True, is_optional=True)
        result = _run(runner, config_file, "run", "--once", "--only", "calendar")
        assert result.exit_code == EXIT_FAILURE


class TestInit:
    def test_writes_a_config_and_the_vault_directories(self, runner, tmp_path):
        target = tmp_path / "new" / "m365-brain.yaml"
        result = runner.invoke(main, ["init", str(target), "--vault", str(tmp_path / "vault")])
        assert result.exit_code == EXIT_OK, result.output
        assert target.is_file()
        for name in ("inbox", "annotations", "outbox", "_meta"):
            assert (tmp_path / "vault" / name).is_dir()

    def test_needs_no_config_option(self, runner, tmp_path):
        result = runner.invoke(main, ["init", str(tmp_path / "c.yaml"), "--vault", str(tmp_path / "v")])
        assert result.exit_code == EXIT_OK

    def test_refuses_to_overwrite(self, runner, tmp_path):
        target = tmp_path / "c.yaml"
        target.write_text("hand written\n", encoding="utf-8")
        result = runner.invoke(main, ["init", str(target), "--vault", str(tmp_path / "v")])
        assert result.exit_code == EXIT_CONFIG
        assert target.read_text(encoding="utf-8") == "hand written\n"

    def test_the_template_it_writes_is_valid_yaml(self, runner, tmp_path):
        target = tmp_path / "c.yaml"
        runner.invoke(main, ["init", str(target), "--vault", str(tmp_path / "v")])
        assert isinstance(yaml.safe_load(target.read_text(encoding="utf-8")), dict)

    def test_it_prints_every_path_it_created(self, runner, tmp_path):
        result = runner.invoke(main, ["init", str(tmp_path / "c.yaml"), "--vault", str(tmp_path / "v")])
        assert len(result.stdout.strip().splitlines()) == 6


class TestConfigVerbs:
    def test_validate_names_the_file(self, runner, config_file):
        assert str(config_file) in _run(runner, config_file, "config", "validate").output

    def test_show_json_parses(self, runner, config_file):
        payload = json.loads(_run(runner, config_file, "config", "show", "--json").stdout)
        assert payload["vault"]["root"]

    def test_show_yaml_parses(self, runner, config_file):
        assert isinstance(yaml.safe_load(_run(runner, config_file, "config", "show").stdout), dict)

    @pytest.fixture()
    def secret_config_file(self, runtime_config, tmp_path):
        """A config file carrying live secret values, written as an operator would.

        The secrets go in as raw YAML rather than through `model_dump`, which
        would have masked them before the verb ever ran -- the point is that
        `config show` loads a real secret and still does not print it.
        """
        payload = runtime_config.model_dump(mode="json")
        payload["auth"]["client_secret"] = SECRET
        payload["storage"]["azure_blob"] = {"connection_string": SECRET, "container_name": "c", "prefix": "p"}
        (tmp_path / "vault").mkdir(exist_ok=True)
        path = tmp_path / "secret.yaml"
        path.write_text(yaml.safe_dump(payload), encoding="utf-8")
        return path

    @pytest.mark.parametrize("form", [("--json",), ()], ids=["json", "yaml"])
    def test_secret_values_never_reach_stdout(self, secret_config_file, runner, form):
        """End-to-end over the verb. The general property lives in test_config_secrets."""
        result = runner.invoke(main, ["--config", str(secret_config_file), "config", "show", *form])
        assert result.exit_code == EXIT_OK, result.output
        assert SECRET not in result.stdout
        assert "**********" in result.stdout

    def test_a_null_secret_stays_null(self, runner, config_file):
        """Which flow a config selects stays readable; only the value is hidden."""
        payload = json.loads(_run(runner, config_file, "config", "show", "--json").stdout)
        assert payload["auth"]["client_secret"] is None


class TestVaultPath:
    def test_inbox_resolves_through_the_configured_directory_name(self, runner, config_file, runtime_config):
        output = _run(runner, config_file, "vault", "path", "inbox", "--extractor", "email").stdout.strip()
        assert output.endswith("/inbox/emails")

    def test_json_carries_both_forms(self, runner, config_file):
        payload = json.loads(
            _run(runner, config_file, "vault", "path", "inbox", "--extractor", "email", "--json").stdout
        )
        assert payload["relative"] == "inbox/emails"

    def test_state_and_manifests_are_filesystem_paths(self, runner, config_file):
        for area in ("state", "manifests"):
            assert _run(runner, config_file, "vault", "path", area).stdout.strip().startswith("/")

    def test_an_unknown_extractor_is_three(self, runner, config_file):
        assert _run(runner, config_file, "vault", "path", "inbox", "--extractor", "nope").exit_code == EXIT_CONFIG


class TestStatus:
    def test_before_any_cycle_it_says_so(self, runner, config_file):
        result = _run(runner, config_file, "status")
        assert "no cycle has completed yet" in result.output

    def test_json_lists_every_enabled_unit(self, runner, config_file):
        payload = json.loads(_run(runner, config_file, "status", "--json").stdout)
        assert set(payload["units"]) == {"email", "calendar", "teams_chats", "index"}
        assert payload["last_cycle"] is None

    def test_reports_the_last_cycle_after_a_run(self, runner, config_file, httpx_mock: HTTPXMock):
        _wire_graph(httpx_mock)
        _run(runner, config_file, "run", "--once")
        payload = json.loads(_run(runner, config_file, "status", "--json").stdout)
        assert payload["last_cycle"]["ok"] is True
        assert payload["units"]["email"]["last_success_at"]


class TestRun:
    def test_a_cycle_prints_a_summary_line(self, runner, config_file, httpx_mock: HTTPXMock):
        _wire_graph(httpx_mock)
        result = _run(runner, config_file, "run", "--once")
        assert result.exit_code == EXIT_OK, result.output
        assert "ok=True" in result.output

    def test_json_emits_the_manifest(self, runner, config_file, httpx_mock: HTTPXMock):
        _wire_graph(httpx_mock)
        payload = json.loads(_run(runner, config_file, "run", "--once", "--json").stdout)
        assert payload["cycle_id"]
        assert payload["extractors"]

    def test_only_narrows_the_units(self, runner, config_file, httpx_mock: HTTPXMock):
        _wire_graph(httpx_mock)
        payload = json.loads(_run(runner, config_file, "run", "--once", "--only", "calendar", "--json").stdout)
        assert [entry["name"] for entry in payload["extractors"]] == ["calendar"]

    def test_an_empty_only_is_three(self, runner, config_file):
        assert _run(runner, config_file, "run", "--once", "--only", " ,").exit_code == EXIT_CONFIG


class TestExtract:
    def test_prints_per_extractor_counts(self, runner, config_file, httpx_mock: HTTPXMock):
        _wire_graph(httpx_mock)
        result = _run(runner, config_file, "extract", "--only", "calendar")
        assert result.exit_code == EXIT_OK, result.output
        assert "calendar\t2 item(s)" in result.output

    def test_does_not_run_the_index_step(self, runner, config_file, httpx_mock: HTTPXMock):
        _wire_graph(httpx_mock)
        payload = json.loads(_run(runner, config_file, "extract", "--only", "calendar", "--json").stdout)
        assert payload["index"] is None

    def test_dry_run_writes_nothing(self, runner, config_file, tmp_path, httpx_mock: HTTPXMock):
        httpx_mock.add_response(url=re.compile(r".*"), json={"value": []}, is_reusable=True, is_optional=True)
        _run(runner, config_file, "extract", "--only", "calendar", "--dry-run")
        assert list((tmp_path / "vault").glob("inbox/**/*.md")) == []


class TestIndexVerbs:
    def test_sync_prints_counters(self, runner, config_file):
        result = _run(runner, config_file, "index", "sync")
        assert result.exit_code == EXIT_OK, result.output
        assert "indexed=" in result.output

    def test_rebuild_requires_yes(self, runner, config_file):
        assert _run(runner, config_file, "index", "rebuild").exit_code == EXIT_USAGE

    def test_search_json_parses(self, runner, config_file, tmp_path):
        (tmp_path / "vault" / "note.md").write_text("---\ntitle: Meeting\n---\nquarterly\n", encoding="utf-8")
        _run(runner, config_file, "index", "sync")
        payload = json.loads(_run(runner, config_file, "index", "search", "quarterly", "--json").stdout)
        assert [hit["title"] for hit in payload["results"]] == ["Meeting"]

    def test_search_respects_limit(self, runner, config_file, tmp_path):
        for number in range(3):
            (tmp_path / "vault" / f"n{number}.md").write_text(f"---\ntitle: N{number}\n---\nshared\n", encoding="utf-8")
        _run(runner, config_file, "index", "sync")
        payload = json.loads(_run(runner, config_file, "index", "search", "shared", "--limit", "2", "--json").stdout)
        assert len(payload["results"]) == 2

    def test_context_needs_exactly_one_selector(self, runner, config_file):
        assert _run(runner, config_file, "index", "context").exit_code == EXIT_USAGE
        assert _run(runner, config_file, "index", "context", "X", "--permalink", "y").exit_code == EXIT_USAGE

    def test_context_on_a_missing_entity_is_not_found(self, runner, config_file):
        """A query matching nothing is a fact about the data, not a broken config."""
        _run(runner, config_file, "index", "sync")
        assert _run(runner, config_file, "index", "context", "nothing-here").exit_code == EXIT_NOT_FOUND

    def test_recent_json_parses(self, runner, config_file, tmp_path):
        (tmp_path / "vault" / "note.md").write_text("---\ntitle: Fresh\n---\nbody\n", encoding="utf-8")
        _run(runner, config_file, "index", "sync")
        payload = json.loads(_run(runner, config_file, "index", "recent", "--json").stdout)
        assert "Fresh" in [entity["title"] for entity in payload["entities"]]

    def test_paths_reports_the_configured_roots(self, runner, config_file):
        payload = json.loads(_run(runner, config_file, "index", "paths", "--json").stdout)
        assert set(payload["roots"]) == {"vault"}
        assert payload["inbox"]["email"] == "inbox/emails"

    def test_catalog_list_is_empty_not_broken(self, runner, config_file):
        _run(runner, config_file, "index", "sync")
        assert json.loads(_run(runner, config_file, "index", "catalog", "list", "--json").stdout)["entries"] == []

    def test_catalog_resolve_on_nothing_is_not_found(self, runner, config_file):
        _run(runner, config_file, "index", "sync")
        assert _run(runner, config_file, "index", "catalog", "resolve", "x").exit_code == EXIT_NOT_FOUND


class TestOutboxVerbs:
    def test_list_without_outboxes_configured_is_three(self, runner, config_file):
        assert _run(runner, config_file, "outbox", "list").exit_code == EXIT_CONFIG

    def test_an_unknown_outbox_is_three(self, runner, config_file):
        assert _run(runner, config_file, "outbox", "push", "--outbox", "nope").exit_code == EXIT_CONFIG


class TestTeamsPost:
    def test_reads_both_ids_out_of_a_channel_url(self):
        url = (
            "https://teams.microsoft.com/l/channel/19%3Aabc123%40thread.tacv2/"
            "General?groupId=11111111-2222-3333-4444-555555555555&tenantId=t"
        )
        assert parse_channel_url(url) == ("11111111-2222-3333-4444-555555555555", "19:abc123@thread.tacv2")

    @pytest.mark.parametrize(
        "url",
        [
            "https://teams.microsoft.com/l/channel/General?groupId=g",
            "https://teams.microsoft.com/l/channel/19%3Aabc%40thread.tacv2/General",
            "not-a-url",
        ],
    )
    def test_a_url_missing_either_id_raises(self, url):
        with pytest.raises(ConfigError, match="cannot read a team id"):
            parse_channel_url(url)

    def test_post_without_the_outbox_configured_is_three(self, runner, config_file, tmp_path):
        body = tmp_path / "body.md"
        body.write_text("hello\n", encoding="utf-8")
        result = _run(
            runner,
            config_file,
            "teams",
            "post",
            "--channel-url",
            "https://teams.microsoft.com/l/channel/19%3Aa%40thread.tacv2/G?groupId=g",
            "--body-file",
            str(body),
            "--created-by",
            "test",
        )
        assert result.exit_code == EXIT_CONFIG


class TestFilesVerbs:
    def test_push_requires_if_match(self, runner, config_file, tmp_path):
        source = tmp_path / "doc.md"
        source.write_text("x", encoding="utf-8")
        result = _run(
            runner,
            config_file,
            "files",
            "push",
            "--profile",
            "p",
            "--site-hostname",
            "h",
            "--site-path",
            "/sites/s",
            "--library",
            "L",
            "--item-path",
            "a.md",
            "--in",
            str(source),
            "--content-type",
            "text/markdown",
        )
        assert result.exit_code == EXIT_USAGE
        assert "--if-match" in result.output


@pytest.mark.parametrize("verb", CONFIG_ONLY, ids=lambda v: " ".join(v))
def test_a_config_only_verb_never_reaches_the_network(runner, config_file, verb, httpx_mock: HTTPXMock):
    """No response is registered; a request would raise rather than hang."""
    result = _run(runner, config_file, *verb)
    assert result.exit_code in (EXIT_OK, EXIT_FAILURE), result.output


class TestOutputContract:
    """Results on stdout, logs on stderr. This is what keeps a caller simple."""

    def test_a_cycle_puts_json_on_stdout_and_logs_on_stderr(self, runner, config_file, httpx_mock: HTTPXMock):
        _wire_graph(httpx_mock)
        result = _run(runner, config_file, "run", "--once", "--json")
        assert json.loads(result.stdout)["cycle_id"]
        assert "cycle.start" in result.stderr
        assert "cycle.start" not in result.stdout

    def test_a_read_verb_puts_nothing_on_stderr(self, runner, config_file):
        result = _run(runner, config_file, "vault", "path", "meta", "--json")
        assert json.loads(result.stdout)["path"]
        assert result.stderr == ""


class TestEveryPrintedPathIsResolved:
    """A path on stdout carries its own base. `emit` is where that happens.

    Doing it at the funnel rather than at each call site is the point: the
    manifest below is dumped straight out of its pydantic model and no line in
    `cli.py` touches its paths, so if what a caller reads is usable it is
    because `emit` made it so -- and a verb added tomorrow gets the same
    treatment without anyone remembering to ask for it.
    """

    def _cycle(self, runner, config_file, httpx_mock: HTTPXMock) -> dict:
        _wire_graph(httpx_mock)
        result = _run(runner, config_file, "run", "--once", "--json")
        assert result.exit_code == EXIT_OK, result.output
        return json.loads(result.stdout)

    def test_the_manifest_a_cycle_prints_names_files_that_exist(self, runner, config_file, httpx_mock: HTTPXMock):
        written = [
            change["path"]
            for entry in self._cycle(runner, config_file, httpx_mock)["extractors"]
            for change in entry["changes"]
            if change["kind"] != "removed"
        ]
        assert written
        assert all(Path(path).is_file() for path in written), written

    def test_the_manifest_on_disk_keeps_the_storage_keys(self, runner, config_file, tmp_path, httpx_mock: HTTPXMock):
        """Resolution is a boundary concern, not a stored one.

        A vault holds tens of thousands of files addressed by relative key.
        Rewriting what is *written* would be a migration; this change is not
        one, and this is the assertion that says so.
        """
        self._cycle(runner, config_file, httpx_mock)
        latest = json.loads((tmp_path / "vault" / "_meta" / "manifests" / "latest.json").read_text(encoding="utf-8"))
        stored = [change["path"] for entry in latest["extractors"] for change in entry["changes"]]
        assert stored
        assert not any(Path(path).is_absolute() for path in stored), stored


def _wire_graph(httpx_mock: HTTPXMock) -> None:
    httpx_mock.add_response(
        url=re.compile(r".*/me/mailFolders/Inbox/messages/delta.*"),
        json=load_fixture("email_response.json"),
        is_reusable=True,
        is_optional=True,
    )
    httpx_mock.add_response(
        url=re.compile(r".*/me/calendarView.*"),
        json=load_fixture("calendar_response.json"),
        is_reusable=True,
        is_optional=True,
    )
    httpx_mock.add_response(url=re.compile(r".*/me/chats\?.*"), json={"value": []}, is_reusable=True, is_optional=True)


class TestLogsNeverReachStdout:
    """stdout is data, stderr is logs -- the two must not mix.

    `logging_config`'s docstring has always stated this, and `configure_logging`
    has always honoured it. The process did not: only `run` and `extract` called
    it, so every other verb ran on structlog's default factory, which writes to
    **stdout**. `outbox list --json` emitted 54 warning lines ahead of its JSON
    and `json.load` raised on output documented as machine-readable.

    Parametrised over the registered verbs rather than a hand-listed few, so a
    verb added tomorrow is covered without anyone remembering to add it.
    """

    @pytest.mark.parametrize("verb", ALL_VERBS, ids=lambda v: " ".join(v))
    def test_any_verb_leaves_structlog_pointed_at_stderr(self, runner, config_file, verb):
        """The group callback sets the floor before a command can emit anything.

        Starts from structlog's stdout-writing default so the assertion fails
        against the pre-fix code rather than passing on a leftover config from
        an earlier test in the same process.
        """
        structlog.configure(logger_factory=structlog.PrintLoggerFactory())
        runner.invoke(main, ["--config", str(config_file), *verb, "--help"])
        assert structlog.get_config()["logger_factory"] is _stderr_logger

    def test_a_json_verb_emits_only_json_on_stdout(self, runner, config_file):
        """The end-to-end shape of the bug: a log line ahead of JSON on one stream."""
        structlog.configure(logger_factory=structlog.PrintLoggerFactory())
        result = runner.invoke(main, ["--config", str(config_file), "index", "paths", "--json"])
        assert result.exit_code == EXIT_OK, result.output
        json.loads(result.stdout)  # raises if anything got in front of the payload


class TestAuthStatusReportsOnlyProfilesInUse:
    """A deployment that names its profiles never authenticates the bare section.

    `_profiles` synthesised `default` from `auth:` unconditionally, and `status`
    exits 4 if *any* profile is unauthenticated -- so a config that resolves
    everything through named profiles had a permanently `never_authenticated`
    phantom and a health verb that failed forever. The shipped template is
    exactly such a config, so every new adopter met it on day one.
    """

    def _named_profiles(self, runtime_config, tmp_path):
        from m365_brain.config import AuthProfileConfig

        profile = AuthProfileConfig(
            client_id="named-client-id",
            tenant_id="test-tenant-id",
            scopes=["Mail.Read"],
            token_cache_path=str(tmp_path / "named_cache.json"),
            client_secret=None,
        )
        auth = runtime_config.auth.model_copy(update={"profiles": {"mail": profile}})
        extractors = runtime_config.extractors.model_copy(update={"auth_profile": "mail"})
        return runtime_config.model_copy(update={"auth": auth, "extractors": extractors})

    def _write(self, config, tmp_path):
        path = tmp_path / "named.yaml"
        path.write_text(yaml.safe_dump(config.model_dump(mode="json"), sort_keys=True), encoding="utf-8")
        return path

    def test_default_is_absent_when_nothing_resolves_through_the_bare_section(self, runner, runtime_config, tmp_path):
        path = self._write(self._named_profiles(runtime_config, tmp_path), tmp_path)
        result = runner.invoke(main, ["--config", str(path), "auth", "status", "--json"])
        names = [entry["name"] for entry in json.loads(result.stdout)["profiles"]]
        assert names == ["mail"], names

    def test_default_survives_when_a_consumer_leaves_auth_profile_unset(self, runner, runtime_config, tmp_path):
        """`auth_profile: null` means "use the auth: section", so it must still report."""
        config = self._named_profiles(runtime_config, tmp_path)
        config = config.model_copy(update={"extractors": config.extractors.model_copy(update={"auth_profile": None})})
        path = self._write(config, tmp_path)
        result = runner.invoke(main, ["--config", str(path), "auth", "status", "--json"])
        names = [entry["name"] for entry in json.loads(result.stdout)["profiles"]]
        assert "default" in names, names
