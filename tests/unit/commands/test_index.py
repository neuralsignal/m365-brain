"""Tests for uncovered paths in `m365_brain/commands/index.py`.

Covers: `rebuild`, `_run_index` error exit, `_narrow` success path,
and `index context` happy path.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
import yaml
from click.testing import CliRunner

from m365_brain.cli import main
from m365_brain.commands._context import EXIT_FAILURE, EXIT_OK
from m365_brain.commands.index import _narrow
from m365_brain.config import ConfigError
from m365_brain.config.index import IndexConfig, IndexRoot
from m365_brain.manifest import IndexOutcome
from m365_brain.model import EntityRef, GraphEdge, Observation


def _write_config(config, tmp_path) -> Path:
    payload = config.model_dump(mode="json")
    (tmp_path / "vault").mkdir(exist_ok=True)
    path = tmp_path / "m365-brain.yaml"
    path.write_text(yaml.safe_dump(payload, sort_keys=True), encoding="utf-8")
    return path


def _run(runner: CliRunner, config_file: Path, *args: str):
    return runner.invoke(main, ["--config", str(config_file), *args])


def _ok_outcome() -> IndexOutcome:
    return IndexOutcome(roots=["vault"], indexed=5, skipped=2, pruned=0, errors=0, elapsed_seconds=0.1)


def _error_outcome() -> IndexOutcome:
    return IndexOutcome(roots=["vault"], indexed=3, skipped=1, pruned=0, errors=1, elapsed_seconds=0.2)


@pytest.fixture(autouse=True)
def offline_tokens(monkeypatch):
    monkeypatch.setattr(
        "m365_brain.commands._context.make_cli_token_provider", lambda auth_config: lambda: "test-token"
    )


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


class TestRebuild:
    def test_rebuild_forwards_full_rebuild_true(self, runner, runtime_config, tmp_path):
        config_file = _write_config(runtime_config, tmp_path)
        with patch("m365_brain.commands.index.run_index_step", return_value=_ok_outcome()) as mock_step:
            result = _run(runner, config_file, "index", "rebuild", "--yes")
        assert result.exit_code == EXIT_OK, result.output
        assert mock_step.call_count == 1
        _, kwargs = mock_step.call_args
        assert kwargs.get("full_rebuild") is True or mock_step.call_args[0][3] is True


class TestRunIndexErrors:
    def test_exits_failure_when_outcome_has_errors(self, runner, runtime_config, tmp_path):
        config_file = _write_config(runtime_config, tmp_path)
        with patch("m365_brain.commands.index.run_index_step", return_value=_error_outcome()):
            result = _run(runner, config_file, "index", "sync")
        assert result.exit_code == EXIT_FAILURE


class TestNarrow:
    def test_returns_matching_roots(self):
        root_a = IndexRoot(name="notes", path="/tmp/notes", recursive=True)
        root_b = IndexRoot(name="archive", path="/tmp/archive", recursive=True)
        index = IndexConfig.model_validate(
            {
                "backend": "sqlite",
                "sqlite": {"path": "/tmp/test.db", "busy_timeout_ms": 5000, "journal_mode": "WAL"},
                "roots": [root_a.model_dump(), root_b.model_dump()],
                "file_extensions": [".md"],
                "exclude": [],
                "sync": {"batch_size": 2, "interval_minutes": 60},
                "frontmatter": {
                    "title_key": "title",
                    "type_key": "type",
                    "permalink_key": "permalink",
                    "tags_key": "tags",
                    "aliases_key": "aliases",
                    "default_type": "note",
                    "structural_keys": ["title", "type", "permalink", "tags"],
                },
                "observations": {"default_category": "Note"},
                "relations": {"explicit_default_type": "relates_to", "inline_type": "links_to"},
                "search": {
                    "page_size": 20,
                    "bm25_weights": {"title": 10.0, "content": 1.0, "tags": 5.0},
                    "snippet": {
                        "column": "content",
                        "start_marker": ">>>",
                        "end_marker": "<<<",
                        "ellipsis": "...",
                        "max_tokens": 40,
                    },
                    "rrf_k": 60,
                    "rrf_min_weight": 0.1,
                    "vector_candidates": 100,
                    "min_similarity": 0.55,
                },
                "catalog": {
                    "conversion_states": ["pending", "eager", "converted", "failed", "skipped"],
                    "initial_state": "pending",
                    "converted_state": "converted",
                    "failed_state": "failed",
                },
                "vector": {
                    "enabled": True,
                    "provider": "hash",
                    "store": "memory",
                    "model": "test-model",
                    "dimensions": 8,
                    "threads": 1,
                    "chunk_size": 900,
                    "chunk_overlap": 120,
                    "embed_batch_size": 32,
                    "write_batch_size": 50,
                },
            }
        )

        result = _narrow(index, ("notes",))
        assert len(result) == 1
        assert result[0].name == "notes"

    def test_returns_multiple_matching_roots(self):
        from tests.unit.conftest import index_payload_for

        payload = index_payload_for(
            Path("/tmp/test.db"),
            [
                {"name": "notes", "path": "/tmp/notes", "recursive": True},
                {"name": "archive", "path": "/tmp/archive", "recursive": True},
            ],
        )
        index = IndexConfig.model_validate(payload)

        result = _narrow(index, ("archive", "notes"))
        assert len(result) == 2
        assert result[0].name == "archive"
        assert result[1].name == "notes"

    def test_raises_on_unknown_root(self):
        from tests.unit.conftest import index_payload_for

        payload = index_payload_for(
            Path("/tmp/test.db"),
            [{"name": "notes", "path": "/tmp/notes", "recursive": True}],
        )
        index = IndexConfig.model_validate(payload)

        with pytest.raises(ConfigError, match="unknown index root"):
            _narrow(index, ("nonexistent",))


class TestContextHappyPath:
    def test_text_output_contains_entity_title(self, runner, runtime_config, tmp_path):
        config_file = _write_config(runtime_config, tmp_path)

        entity = EntityRef(
            entity_id=1,
            key="vault/note.md",
            title="My Note",
            entity_type="note",
            permalink="note-1",
            file_path="vault/note.md",
            updated_at="2026-01-01T00:00:00Z",
        )
        observations = [
            Observation(category="status", content="active", tags=["important"], context=None),
        ]
        edges = [
            GraphEdge(
                depth=1,
                direction="outgoing",
                from_entity_id=1,
                to_entity_id=2,
                to_name="Other",
                relation_type="relates_to",
            ),
        ]

        with (
            patch("m365_brain.commands.index.open_workspace") as mock_ws,
        ):
            ws = mock_ws.return_value.__enter__.return_value
            ws.find.return_value = entity
            ws.observations.return_value = observations
            ws.context.return_value = edges

            result = _run(runner, config_file, "index", "context", "note-1")

        assert result.exit_code == EXIT_OK, result.output
        assert "My Note" in result.output
        assert "note-1" in result.output
        assert "[status] active" in result.output
        assert "relates_to" in result.output
        assert "Other" in result.output

    def test_json_output_contains_entity_data(self, runner, runtime_config, tmp_path):
        import json

        config_file = _write_config(runtime_config, tmp_path)

        entity = EntityRef(
            entity_id=1,
            key="vault/note.md",
            title="My Note",
            entity_type="note",
            permalink="note-1",
            file_path="vault/note.md",
            updated_at="2026-01-01T00:00:00Z",
        )

        with patch("m365_brain.commands.index.open_workspace") as mock_ws:
            ws = mock_ws.return_value.__enter__.return_value
            ws.find.return_value = entity
            ws.observations.return_value = []
            ws.context.return_value = []

            result = _run(runner, config_file, "index", "context", "note-1", "--format", "json")

        assert result.exit_code == EXIT_OK, result.output
        payload = json.loads(result.stdout)
        assert payload["entity"]["title"] == "My Note"
        assert payload["entity"]["permalink"] == "note-1"
