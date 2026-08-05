"""`index catalog …` against a catalog that has rows in it.

The existing CLI suite asserts that these verbs are "empty, not broken". That
is all it could assert -- nothing populated the catalog, so every query returned
zero rows and every query therefore looked correct. A query written against a
table nobody ever filled is a query nobody ever ran, and four of them were
wrong:

  * `search` passed its argument into `LIKE` unescaped, so `_` matched any
    character and `%` matched every row in the table
  * `--ext PDF` matched nothing, because registration lower-cases the suffix
    and the filter did not
  * the in-memory and SQLite backends disagreed on both of the above, so the
    same query answered differently depending on `index.backend`
  * `resolve` reported its capped sample size as if it were the total, and
    called an exact filename match ambiguous whenever a longer name shared it

Each of those has a test below named after the symptom.
"""

from __future__ import annotations

import json

import pytest
import yaml
from click.testing import CliRunner

from m365_brain.cli import main
from m365_brain.commands._context import EXIT_CONFIG, EXIT_OK, EXIT_USAGE
from m365_brain.index.backends import create_index_backend
from m365_brain.index.catalog import FileCatalog
from m365_brain.model import CatalogEntry

ATTACHMENTS = "inbox/emails/2026-03/note/attachments"

# (path under ATTACHMENTS, source, bytes)
CORPUS = [
    ("annual_report.pdf", "email", b"%PDF annual"),
    ("annualXreport.pdf", "email", b"%PDF decoy"),
    ("SUMMARY.PDF", "onedrive", b"%PDF summary"),
    ("notes.txt", "email", b"plain notes"),
]


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture()
def populated(runtime_config, tmp_path):
    """A config file whose catalog holds `CORPUS`, and the files to match."""
    vault = tmp_path / "vault"
    vault.mkdir(exist_ok=True)
    backend = create_index_backend(runtime_config.index)
    backend.initialize()
    catalog = FileCatalog(backend, runtime_config.index.catalog)
    try:
        for index, (name, source, payload) in enumerate(CORPUS):
            path = f"{ATTACHMENTS}/{name}"
            (vault / path).parent.mkdir(parents=True, exist_ok=True)
            (vault / path).write_bytes(payload)
            catalog.upsert(
                CatalogEntry(
                    entry_id=None,
                    original_path=path,
                    file_name=name,
                    extension=f".{name.rsplit('.', 1)[1].lower()}",
                    source=source,
                    size_bytes=len(payload),
                    modified_at=f"2026-03-{index + 1:02d}T00:00:00Z",
                    conversion_status=runtime_config.index.catalog.initial_state,
                    output_path=None,
                    error=None,
                )
            )
    finally:
        backend.close()

    path = tmp_path / "m365-brain.yaml"
    path.write_text(yaml.safe_dump(runtime_config.model_dump(mode="json"), sort_keys=True), encoding="utf-8")
    return path


def _run(runner: CliRunner, config_file, *args: str):
    return runner.invoke(main, ["--config", str(config_file), *args])


def _names(result) -> list[str]:
    assert result.exit_code == EXIT_OK, result.output
    return [entry["file_name"] for entry in json.loads(result.stdout)["entries"]]


class TestList:
    def test_every_row_comes_back_newest_first(self, runner, populated):
        assert _names(_run(runner, populated, "index", "catalog", "list", "--json")) == [
            "notes.txt",
            "SUMMARY.PDF",
            "annualXreport.pdf",
            "annual_report.pdf",
        ]

    def test_the_source_filter_narrows(self, runner, populated):
        names = _names(_run(runner, populated, "index", "catalog", "list", "--source", "onedrive", "--json"))
        assert names == ["SUMMARY.PDF"]

    @pytest.mark.parametrize("spelling", [".pdf", "pdf", ".PDF", "PDF"])
    def test_the_extension_filter_ignores_case_and_the_leading_dot(self, runner, populated, spelling):
        """`--ext PDF` used to return nothing at all for a vault full of PDFs."""
        names = _names(_run(runner, populated, "index", "catalog", "list", "--ext", spelling, "--json"))
        assert sorted(names) == ["SUMMARY.PDF", "annualXreport.pdf", "annual_report.pdf"]

    def test_the_limit_is_honoured(self, runner, populated):
        assert len(_names(_run(runner, populated, "index", "catalog", "list", "--limit", "2", "--json"))) == 2

    def test_stats_counts_every_configured_state(self, runner, populated, runtime_config):
        result = _run(runner, populated, "index", "catalog", "list", "--stats", "--json")
        counts = json.loads(result.stdout)
        assert counts["total"] == len(CORPUS)
        assert counts[runtime_config.index.catalog.initial_state] == len(CORPUS)
        assert set(counts) == {"total", *runtime_config.index.catalog.conversion_states}

    def test_stats_beside_a_filter_is_a_usage_error(self, runner, populated):
        """It counted the whole table regardless, which reads as an answer."""
        result = _run(runner, populated, "index", "catalog", "list", "--stats", "--source", "email")
        assert result.exit_code == EXIT_USAGE


class TestSearch:
    def test_a_substring_matches(self, runner, populated):
        assert _names(_run(runner, populated, "index", "catalog", "search", "notes", "--json")) == ["notes.txt"]

    def test_the_query_is_case_insensitive(self, runner, populated):
        assert _names(_run(runner, populated, "index", "catalog", "search", "summary", "--json")) == ["SUMMARY.PDF"]

    def test_an_underscore_is_a_literal_underscore(self, runner, populated):
        """`_` is a LIKE wildcard, so this used to match `annualXreport.pdf` too."""
        names = _names(_run(runner, populated, "index", "catalog", "search", "annual_report", "--json"))
        assert names == ["annual_report.pdf"]

    def test_a_percent_matches_nothing_rather_than_everything(self, runner, populated):
        """Unescaped, `%` returned the entire catalog as if it were a match."""
        assert _names(_run(runner, populated, "index", "catalog", "search", "%", "--json")) == []

    def test_a_status_filter_narrows(self, runner, populated, runtime_config):
        state = runtime_config.index.catalog.converted_state
        assert (
            _names(_run(runner, populated, "index", "catalog", "search", "report", "--status", state, "--json")) == []
        )


class TestResolve:
    def test_a_unique_match_resolves_to_its_path(self, runner, populated):
        result = _run(runner, populated, "index", "catalog", "resolve", "notes", "--json")
        assert result.exit_code == EXIT_OK, result.output
        assert json.loads(result.stdout)["original_path"] == f"{ATTACHMENTS}/notes.txt"

    def test_an_exact_filename_wins_over_the_longer_names_containing_it(self, runner, populated):
        """`annual_report.pdf` is not ambiguous just because it is a substring."""
        result = _run(runner, populated, "index", "catalog", "resolve", "annual_report.pdf", "--json")
        assert result.exit_code == EXIT_OK, result.output
        assert json.loads(result.stdout)["original_path"] == f"{ATTACHMENTS}/annual_report.pdf"

    def test_a_genuinely_ambiguous_query_is_an_error_naming_the_paths(self, runner, populated):
        result = _run(runner, populated, "index", "catalog", "resolve", "report")
        assert result.exit_code == EXIT_CONFIG
        assert "matches 2 files" in result.output
        assert f"{ATTACHMENTS}/annual_report.pdf" in result.output

    def test_no_match_is_an_error(self, runner, populated):
        assert _run(runner, populated, "index", "catalog", "resolve", "nothing-like-this").exit_code == EXIT_CONFIG


class TestExtract:
    def test_a_pass_converts_the_pending_rows_and_reports_the_counts(self, runner, populated, monkeypatch):
        monkeypatch.setattr(
            "m365_brain.m365.converters.document.convert_document",
            lambda file_path, converters_config: f"# {file_path.name}",
        )
        result = _run(runner, populated, "index", "catalog", "extract", "--json")

        assert result.exit_code == EXIT_OK, result.output
        assert json.loads(result.stdout) == {"attempted": 4, "converted": 4, "failed": 0}

    def test_the_markdown_lands_in_the_converted_sibling(self, runner, populated, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "m365_brain.m365.converters.document.convert_document",
            lambda file_path, converters_config: f"# {file_path.name}",
        )
        _run(runner, populated, "index", "catalog", "extract")

        converted = tmp_path / "vault" / ATTACHMENTS.replace("attachments", "attachments_converted") / "notes.txt.md"
        assert converted.read_text() == "# notes.txt"

    def test_a_second_pass_has_nothing_left_to_do(self, runner, populated, monkeypatch):
        monkeypatch.setattr(
            "m365_brain.m365.converters.document.convert_document",
            lambda file_path, converters_config: "# converted",
        )
        _run(runner, populated, "index", "catalog", "extract")
        result = _run(runner, populated, "index", "catalog", "extract", "--json")

        assert json.loads(result.stdout) == {"attempted": 0, "converted": 0, "failed": 0}

    def test_a_failure_is_recorded_on_the_row_and_not_retried(self, runner, populated, monkeypatch):
        from m365_brain.m365.converters.document import DocumentConversionError

        def explode(file_path, converters_config):
            raise DocumentConversionError("encrypted")

        monkeypatch.setattr("m365_brain.m365.converters.document.convert_document", explode)
        first = _run(runner, populated, "index", "catalog", "extract", "--json")
        second = _run(runner, populated, "index", "catalog", "extract", "--json")

        assert json.loads(first.stdout)["failed"] == 4
        assert json.loads(second.stdout)["attempted"] == 0, "a failed row is terminal without --retry-failed"

        rows = json.loads(_run(runner, populated, "index", "catalog", "list", "--json").stdout)["entries"]
        assert all("encrypted" in row["error"] for row in rows)
        assert all(row["output_path"] is None for row in rows)

    def test_retry_failed_puts_them_back(self, runner, populated, monkeypatch):
        def explode(file_path, converters_config):
            from m365_brain.m365.converters.document import DocumentConversionError

            raise DocumentConversionError("encrypted")

        monkeypatch.setattr("m365_brain.m365.converters.document.convert_document", explode)
        _run(runner, populated, "index", "catalog", "extract")

        monkeypatch.setattr(
            "m365_brain.m365.converters.document.convert_document",
            lambda file_path, converters_config: "# recovered",
        )
        result = _run(runner, populated, "index", "catalog", "extract", "--retry-failed", "--json")
        assert json.loads(result.stdout) == {"attempted": 4, "converted": 4, "failed": 0}

    def test_the_limit_caps_one_pass(self, runner, populated, monkeypatch):
        monkeypatch.setattr(
            "m365_brain.m365.converters.document.convert_document",
            lambda file_path, converters_config: "# converted",
        )
        result = _run(runner, populated, "index", "catalog", "extract", "--limit", "2", "--json")
        assert json.loads(result.stdout)["attempted"] == 2

    def test_a_blob_backed_vault_says_why_it_cannot(self, runner, populated, runtime_config, tmp_path):
        """`StorageBackend` can write bytes but not read them. Say so, do not guess."""
        payload = runtime_config.model_dump(mode="json")
        payload["storage"] = {
            "backend": "azure_blob",
            "local": None,
            "azure_blob": {"connection_string": "x", "container_name": "c", "prefix": "p"},
        }
        config_file = tmp_path / "blob.yaml"
        config_file.write_text(yaml.safe_dump(payload, sort_keys=True), encoding="utf-8")

        result = _run(runner, config_file, "index", "catalog", "extract")
        assert result.exit_code == EXIT_CONFIG
        assert "read_bytes" in result.output
