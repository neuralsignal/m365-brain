"""The workspace-reference checker must catch leaks without tripping on the package's own name.

The package is called `m365_brain`, and the workspace it was extracted from was
called Brain. A substring match would reject every line mentioning the package
itself, so the pattern carries a word boundary. That boundary is the single
most load-bearing detail in the script, and the reason these tests exist.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_no_workspace.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_no_workspace", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cnw = _load()


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "readme.md").write_text("A Microsoft 365 extraction package.\n")
    return tmp_path


def _write(tree: Path, name: str, text: str) -> None:
    (tree / "docs" / name).write_text(text)


LEAKS = [
    "see the Brain workspace for details",
    "the brain workspace",
    "imported from brain_db",
    "the brain-knowledge skill",
    "the brain-files skill",
    "the brain-ops skill",
    "packages/agentic-brain/docs",
    "state lives in .brain/index.db",
    "written to knowledge/notes/inbox/",
    "one file per contact in knowledge/people/",
    "tracked in knowledge/goals/",
    "tracked in knowledge/tasks/",
    "the m365-data-sync daemon",
    "from m365_data_sync import reconcile",
    "superseded sanoptis-sources ADR 0010",
]


@pytest.mark.parametrize("line", LEAKS)
def test_leak_is_caught(tree: Path, line: str):
    _write(tree, "leak.md", line + "\n")
    assert cnw.scan(tree), f"missed: {line!r}"


SAFE = [
    "install m365-brain from PyPI",
    "from m365_brain import workspace",
    "run `m365-brain run --config m365-brain.yaml`",
    "the package directory is m365_brain/",
    "start a brainstorm before implementing",
    "no-brainer defaults are still defaults",
]


@pytest.mark.parametrize("line", SAFE)
def test_package_own_name_does_not_trip(tree: Path, line: str):
    _write(tree, "safe.md", line + "\n")
    assert cnw.scan(tree) == [], f"false positive on: {line!r}"


def test_clean_tree_is_clean(tree: Path):
    assert cnw.scan(tree) == []


def test_changelog_is_exempt(tree: Path):
    """release-please owns it and release history is immutable."""
    (tree / "CHANGELOG.md").write_text("* renamed from the Brain workspace era\n")
    assert cnw.scan(tree) == []


def test_the_checker_itself_is_exempt(tree: Path):
    """Its pattern table necessarily spells out what it rejects."""
    assert cnw.SCRIPT_NAME in cnw.EXEMPT_FILES


def test_lock_files_are_exempt(tree: Path):
    (tree / "pixi.lock").write_text("# path: /home/x/Brain/external\n")
    assert cnw.scan(tree) == []


def test_binary_file_does_not_crash(tree: Path):
    (tree / "docs" / "blob.md").write_bytes(b"\xff\xfe\x00binary Brain\x00")
    cnw.scan(tree)  # must not raise


def test_task_documents_are_exempt(tree: Path):
    """Trellis task documents legitimately discuss the consumer."""
    tasks = tree / ".trellis" / "tasks" / "some-task"
    tasks.mkdir(parents=True)
    (tasks / "prd.md").write_text("migrate the Brain workspace consumers\n")
    assert cnw.scan(tree) == []


class TestPackagePrefixedNames:
    """This repo's own skills are `m365-brain-*` and must not be findings."""

    @pytest.mark.parametrize(
        "line",
        [
            "name: m365-brain-knowledge",
            "name: m365-brain-files",
            "name: m365-brain-ops",
            "skills/m365-brain-ops/references/config-keys.md",
            "from m365_brain.ops import tiers",
        ],
    )
    def test_the_packages_own_names_pass(self, tmp_path: Path, line: str) -> None:
        (tmp_path / "SKILL.md").write_text(line + "\n", encoding="utf-8")
        assert cnw.scan(tmp_path) == []

    @pytest.mark.parametrize("line", ["name: brain-knowledge", "brain_files.search", "brain-ops/scripts/x.py"])
    def test_the_unprefixed_originals_are_still_rejected(self, tmp_path: Path, line: str) -> None:
        (tmp_path / "SKILL.md").write_text(line + "\n", encoding="utf-8")
        assert cnw.scan(tmp_path) != []
