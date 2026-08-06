"""A capped answer has to say it was capped.

`index catalog list --json` against a 900-row catalog returned exactly 100 rows
in a bare `{"entries": [...]}`. No total, no echo of the cap, nothing in the
response distinguishing "here is everything" from "here is the first ninth of
it". That is the `$top` defect wearing different clothes -- a call that succeeds
while returning a fraction -- and the caller's only defence was to already
suspect it.

Five verbs carry a `--limit`, and the set is **discovered from the shipped
command tree** rather than hand-listed, so a sixth added tomorrow fails
`test_every_capped_verb_is_covered` on arrival instead of shipping unguarded.
Each is then driven through the real CLI against a real store: the property is
what a caller parses, not what a helper returns.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import click
import pytest
import yaml
from click.testing import CliRunner

from m365_brain.cli import main
from m365_brain.commands._context import EXIT_OK
from m365_brain.index.backends import create_index_backend
from m365_brain.index.catalog import FileCatalog
from m365_brain.model import CatalogEntry

ATTACHMENTS = "inbox/emails/2026-03/note/attachments"
CORPUS_SIZE = 3
"""Notes indexed, and catalog rows registered. Any number above one will do:
the assertion is `returned < total`, not a particular pair of numbers."""

# One runnable invocation per capped verb, minus the `--limit` and `--json`
# the tests append. The corpus below is built so that every one of them
# matches more rows than a limit of one can return.
CAPPED_VERBS: dict[tuple[str, ...], list[str]] = {
    ("index", "search"): ["index", "search", "shared"],
    ("index", "recent"): ["index", "recent"],
    ("index", "catalog", "list"): ["index", "catalog", "list"],
    ("index", "catalog", "search"): ["index", "catalog", "search", "file"],
    ("index", "catalog", "extract"): ["index", "catalog", "extract"],
}

ROWS_KEY = {
    ("index", "search"): "results",
    ("index", "recent"): "entities",
    ("index", "catalog", "list"): "entries",
    ("index", "catalog", "search"): "entries",
}
"""Where each verb puts its rows. `catalog extract` has none -- it reports
counters -- which is why `returned` rather than the row count is the field the
envelope carries."""


def _capped(command: click.Command, path: tuple[str, ...] = ()) -> Iterator[tuple[str, ...]]:
    """Every registered verb taking a `--limit`, read off the command tree."""
    if isinstance(command, click.Group):
        for name, sub in command.commands.items():
            yield from _capped(sub, (*path, name))
        return
    if any(param.name == "limit" for param in command.params):
        yield path


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture(autouse=True)
def a_converter_that_always_succeeds(monkeypatch):
    """`catalog extract` is a capped verb too, and it needs a converter."""
    monkeypatch.setattr(
        "m365_brain.m365.converters.document.convert_document",
        lambda file_path, converters_config: f"# {file_path.name}",
    )


@pytest.fixture()
def corpus(runtime_config, tmp_path, runner):
    """`CORPUS_SIZE` indexed notes and `CORPUS_SIZE` catalogued files."""
    vault = tmp_path / "vault"
    vault.mkdir(exist_ok=True)
    for number in range(CORPUS_SIZE):
        (vault / f"note{number}.md").write_text(f"---\ntitle: Note {number}\n---\nshared\n", encoding="utf-8")

    backend = create_index_backend(runtime_config.index)
    backend.initialize()
    catalog = FileCatalog(backend, runtime_config.index.catalog)
    try:
        for number in range(CORPUS_SIZE):
            name = f"file{number}.txt"
            path = f"{ATTACHMENTS}/{name}"
            (vault / path).parent.mkdir(parents=True, exist_ok=True)
            (vault / path).write_bytes(b"payload")
            catalog.upsert(
                CatalogEntry(
                    entry_id=None,
                    original_path=path,
                    file_name=name,
                    extension=".txt",
                    extractor="email",
                    size_bytes=7,
                    modified_at=f"2026-03-{number + 1:02d}T00:00:00Z",
                    conversion_status=runtime_config.index.catalog.initial_state,
                    output_path=None,
                    error=None,
                )
            )
    finally:
        backend.close()

    config_path = tmp_path / "m365-brain.yaml"
    config_path.write_text(yaml.safe_dump(runtime_config.model_dump(mode="json"), sort_keys=True), encoding="utf-8")
    runner.invoke(main, ["--config", str(config_path), "index", "sync"])
    return config_path


def _run(runner: CliRunner, config_path, *args: str):
    return runner.invoke(main, ["--config", str(config_path), *args])


def test_every_capped_verb_is_covered():
    """The table is checked against the tree, so it cannot fall behind it."""
    assert set(_capped(main)) == set(CAPPED_VERBS), "a verb grew a --limit without a truncation guard"


@pytest.mark.parametrize("verb", sorted(CAPPED_VERBS), ids=lambda v: " ".join(v))
def test_a_capped_response_carries_the_total_it_was_capped_from(runner, corpus, verb):
    """`returned < total` is the whole property, and it is one shape everywhere."""
    result = _run(runner, corpus, *CAPPED_VERBS[verb], "--limit", "1", "--json")
    assert result.exit_code == EXIT_OK, result.output

    payload = json.loads(result.stdout)
    assert {"total", "returned", "limit"} <= set(payload), f"{verb} hides its cap: {sorted(payload)}"
    assert payload["limit"] == 1
    assert payload["returned"] == 1
    assert payload["total"] == CORPUS_SIZE
    assert payload["returned"] < payload["total"], "the caller cannot detect truncation from this response"


@pytest.mark.parametrize("verb", sorted(ROWS_KEY), ids=lambda v: " ".join(v))
def test_returned_counts_the_rows_actually_present(runner, corpus, verb):
    payload = json.loads(_run(runner, corpus, *CAPPED_VERBS[verb], "--limit", "1", "--json").stdout)
    assert len(payload[ROWS_KEY[verb]]) == payload["returned"]


@pytest.mark.parametrize("verb", sorted(CAPPED_VERBS), ids=lambda v: " ".join(v))
def test_the_human_output_says_so_too(runner, corpus, verb):
    """A truncated list that reads as complete is the defect in either format."""
    result = _run(runner, corpus, *CAPPED_VERBS[verb], "--limit", "1")
    assert result.exit_code == EXIT_OK, result.output
    assert f"truncated: 1 of {CORPUS_SIZE}" in result.stdout, result.stdout


@pytest.mark.parametrize("verb", sorted(CAPPED_VERBS), ids=lambda v: " ".join(v))
def test_a_complete_answer_is_not_annotated(runner, corpus, verb):
    """The note has to mean something, so it cannot be printed unconditionally."""
    result = _run(runner, corpus, *CAPPED_VERBS[verb], "--limit", str(CORPUS_SIZE + 1))
    assert result.exit_code == EXIT_OK, result.output
    assert "truncated" not in result.stdout


class TestTheDefaultLimitIsAConfigValue:
    """Five flags, four silent defaults -- `None`, 20, 100, 100, 100 -- and no
    `--help` anywhere naming one. A caller could not learn the cap without
    reading the source, which is the same fact the response was hiding."""

    @pytest.mark.parametrize("verb", sorted(CAPPED_VERBS), ids=lambda v: " ".join(v))
    def test_help_names_the_configured_default(self, runner, verb):
        output = runner.invoke(main, [*verb, "--help"]).output
        assert "index.search.page_size" in output, output

    @pytest.mark.parametrize("verb", sorted(ROWS_KEY), ids=lambda v: " ".join(v))
    def test_an_omitted_limit_reports_the_page_size_it_used(self, runner, corpus, verb, runtime_config):
        payload = json.loads(_run(runner, corpus, *CAPPED_VERBS[verb], "--json").stdout)
        assert payload["limit"] == runtime_config.index.search.page_size


class TestSearchLimitIsNotCappedByThePageSize:
    """`index search --limit 100` returned 20 hits of 23,012 and said nothing.

    The flag trimmed a page that `index.search.page_size` had already capped,
    so any limit above the configured size could not be reached -- the flag
    was accepted, ignored, and silent about it. It now sizes the page.
    """

    def test_a_limit_above_the_page_size_is_honoured(self, runner, corpus, runtime_config):
        wanted = runtime_config.index.search.page_size + 5
        payload = json.loads(_run(runner, corpus, "index", "search", "shared", "--limit", str(wanted), "--json").stdout)
        assert payload["limit"] == wanted
        assert payload["returned"] == CORPUS_SIZE, "every match fits well inside the raised limit"


class TestRecentNarrowsBeforeItCaps:
    """`--type` filtered the rows *after* the limit had already picked them.

    `index recent --type task --limit 20` therefore meant "the tasks among the
    twenty newest entities of any type" while reading as "the twenty newest
    tasks" -- a wrong answer that looks like a small corpus, and one that would
    have made `total` disagree with the rows it was printed beside.
    """

    def test_the_type_filter_reaches_the_query(self, runner, corpus, tmp_path):
        (tmp_path / "vault" / "task.md").write_text("---\ntitle: T\ntype: task\n---\nshared\n", encoding="utf-8")
        _run(runner, corpus, "index", "sync")

        payload = json.loads(_run(runner, corpus, "index", "recent", "--type", "task", "--limit", "1", "--json").stdout)

        assert [row["type"] for row in payload["entities"]] == ["task"]
        assert payload["total"] == 1, "the total counts what the query matched, not the whole corpus"
