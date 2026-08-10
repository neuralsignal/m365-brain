"""`files pull` and `files push` -- success, failure, and structured output.

Both verbs are thin CLI wrappers: they resolve a SharePoint site and drive,
then delegate to `get_file` or `update_file`. The Graph layer and auth are
mocked so these tests exercise the wiring and output contract only.
"""

from __future__ import annotations

import json
import tempfile
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml
from click.testing import CliRunner
from hypothesis import given
from hypothesis import strategies as st

from m365_brain.cli import main
from m365_brain.commands._context import EXIT_CONFIG, EXIT_OK
from m365_brain.config.runtime import M365Config, UploadConfig

LOCATION_ARGS = [
    "--profile",
    "test-profile",
    "--site-hostname",
    "contoso.sharepoint.com",
    "--site-path",
    "/sites/Team",
    "--library",
    "Documents",
    "--item-path",
    "Reports/annual.md",
]


@pytest.fixture()
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture()
def config_file(runtime_config, tmp_path):
    (tmp_path / "vault").mkdir(exist_ok=True)
    path = tmp_path / "m365-brain.yaml"
    path.write_text(yaml.safe_dump(runtime_config.model_dump(mode="json"), sort_keys=True), encoding="utf-8")
    return path


@pytest.fixture()
def config_with_m365(runtime_config, tmp_path):
    """Config with an `m365:` section so `push` can read `upload`."""
    upload = UploadConfig(
        inline_attachment_max_bytes=2_000_000,
        simple_upload_max_bytes=4_000_000,
        chunk_bytes=320 * 1024,
    )
    config = runtime_config.model_copy(update={"m365": M365Config(upload=upload)})
    (tmp_path / "vault").mkdir(exist_ok=True)
    path = tmp_path / "m365-brain.yaml"
    path.write_text(yaml.safe_dump(config.model_dump(mode="json"), sort_keys=True), encoding="utf-8")
    return path


def _run(runner: CliRunner, config_file, *args: str):
    return runner.invoke(main, ["--config", str(config_file), *args])


@contextmanager
def _mock_graph(**extra_patches):
    """Patch the three seams: `_client`, `resolve_site_id`, `resolve_drive_id`.

    Extra patches (e.g. `get_file=...`) are applied alongside the graph mocks.
    """
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)

    with (
        patch("m365_brain.commands.files._client", return_value=mock_client) as client_fn,
        patch("m365_brain.commands.files.resolve_site_id", return_value="site-id-123") as site_fn,
        patch("m365_brain.commands.files.resolve_drive_id", return_value=("drive-id-456", False)) as drive_fn,
    ):
        extra_mocks = {}
        ctx_stack = []
        for name, return_value in extra_patches.items():
            p = patch(f"m365_brain.commands.files.{name}", return_value=return_value)
            mock = p.start()
            ctx_stack.append(p)
            extra_mocks[name] = mock
        try:
            yield client_fn, site_fn, drive_fn, mock_client, extra_mocks
        finally:
            for p in ctx_stack:
                p.stop()


class TestPull:
    def test_success_writes_file_and_emits_etag(self, runner, config_file, tmp_path):
        out = tmp_path / "out" / "annual.md"
        with _mock_graph(get_file=("# Annual Report", '"etag-abc"')):
            result = _run(runner, config_file, "files", "pull", *LOCATION_ARGS, "--out", str(out))

        assert result.exit_code == EXIT_OK, result.output
        assert out.read_text(encoding="utf-8") == "# Annual Report"
        assert "etag-abc" in result.output

    def test_not_found_exits_with_system_exit(self, runner, config_file, tmp_path):
        out = tmp_path / "out" / "missing.md"
        with _mock_graph(get_file=None):
            result = _run(runner, config_file, "files", "pull", *LOCATION_ARGS, "--out", str(out))

        assert result.exit_code != EXIT_OK
        assert "no document" in result.output

    def test_json_emits_structured_output(self, runner, config_file, tmp_path):
        out = tmp_path / "pulled.md"
        with _mock_graph(get_file=("hello", '"etag-1"')):
            result = _run(runner, config_file, "files", "pull", *LOCATION_ARGS, "--out", str(out), "--json")

        assert result.exit_code == EXIT_OK, result.output
        payload = json.loads(result.stdout)
        assert payload["etag"] == '"etag-1"'
        assert payload["bytes"] == len(b"hello")
        assert payload["path"] == str(out.resolve())

    def test_resolves_site_and_drive_with_location_args(self, runner, config_file, tmp_path):
        out = tmp_path / "doc.md"
        with _mock_graph(get_file=("x", '"e"')) as (_, site_fn, drive_fn, _, _):
            _run(runner, config_file, "files", "pull", *LOCATION_ARGS, "--out", str(out))

        site_fn.assert_called_once()
        args = site_fn.call_args
        assert args[0][1] == "contoso.sharepoint.com"
        assert args[0][2] == "/sites/Team"

        drive_fn.assert_called_once()
        assert drive_fn.call_args[0][1] == "site-id-123"
        assert drive_fn.call_args[0][2] == "Documents"

    def test_creates_parent_directories(self, runner, config_file, tmp_path):
        out = tmp_path / "deeply" / "nested" / "dir" / "file.md"
        with _mock_graph(get_file=("content", '"e"')):
            result = _run(runner, config_file, "files", "pull", *LOCATION_ARGS, "--out", str(out))

        assert result.exit_code == EXIT_OK, result.output
        assert out.is_file()


class TestPush:
    def test_success_calls_update_file_and_emits_new_etag(self, runner, config_with_m365, tmp_path):
        source = tmp_path / "doc.md"
        source.write_text("updated content", encoding="utf-8")
        with _mock_graph(update_file='"etag-new"') as (_, _, _, _, extra):
            result = _run(
                runner,
                config_with_m365,
                "files",
                "push",
                *LOCATION_ARGS,
                "--in",
                str(source),
                "--if-match",
                '"etag-old"',
                "--content-type",
                "text/markdown",
            )

        assert result.exit_code == EXIT_OK, result.output
        assert "etag-new" in result.output
        update_fn = extra["update_file"]
        update_fn.assert_called_once()
        call_args = update_fn.call_args[0]
        assert call_args[2] == "drive-id-456"
        assert call_args[3] == "Reports/annual.md"
        assert call_args[4] == b"updated content"
        assert call_args[5] == "text/markdown"
        assert call_args[6] == '"etag-old"'

    def test_json_emits_structured_output(self, runner, config_with_m365, tmp_path):
        source = tmp_path / "doc.md"
        source.write_text("data", encoding="utf-8")
        with _mock_graph(update_file='"etag-2"'):
            result = _run(
                runner,
                config_with_m365,
                "files",
                "push",
                *LOCATION_ARGS,
                "--in",
                str(source),
                "--if-match",
                '"e"',
                "--content-type",
                "text/markdown",
                "--json",
            )

        assert result.exit_code == EXIT_OK, result.output
        payload = json.loads(result.stdout)
        assert payload["etag"] == '"etag-2"'

    def test_missing_m365_section_raises_config_error(self, runner, config_file, tmp_path):
        source = tmp_path / "doc.md"
        source.write_text("x", encoding="utf-8")
        with _mock_graph():
            result = _run(
                runner,
                config_file,
                "files",
                "push",
                *LOCATION_ARGS,
                "--in",
                str(source),
                "--if-match",
                '"e"',
                "--content-type",
                "text/markdown",
            )

        assert result.exit_code == EXIT_CONFIG
        assert "m365" in result.output


class TestContentRoundTrip:
    @given(content=st.text(min_size=1, max_size=500))
    def test_any_content_round_trips_through_write_text(self, content):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".md", delete=True) as f:
            out_path = Path(f.name)
        out_path.write_text(content, encoding="utf-8")
        try:
            assert out_path.read_bytes() == content.encode("utf-8")
        finally:
            out_path.unlink(missing_ok=True)
