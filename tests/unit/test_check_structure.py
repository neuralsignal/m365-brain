"""The structure checker must fail on every rule it claims to enforce.

An unverified linter turns "no findings" from evidence into noise, and a
structure check is exactly the kind of script that silently stops matching
after a refactor. Each test below plants one violation and asserts the
corresponding check sees it.

The script is loaded by path rather than imported, because `scripts/` is not a
package and `sys.path` manipulation is one of the things the checker rejects.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_structure.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_structure", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


cs = _load()


@pytest.fixture
def clean_tree(tmp_path: Path) -> Path:
    """A minimal tree that satisfies every rule."""
    pkg = tmp_path / cs.PACKAGE
    (pkg / "index").mkdir(parents=True)
    (pkg / "m365").mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "config.py").write_text("VALUE = 1\n")
    (pkg / "cli.py").write_text(f"from {cs.PACKAGE} import config\n")
    (pkg / "index" / "__init__.py").write_text("")
    (pkg / "index" / "sync.py").write_text(f"from {cs.PACKAGE} import config\n")
    (pkg / "m365" / "__init__.py").write_text("")
    (pkg / "m365" / "client.py").write_text("X = 1\n")

    unit = tmp_path / "tests" / "unit"
    (unit / "index").mkdir(parents=True)
    (unit / "m365").mkdir()
    (unit / "index" / "test_sync.py").write_text("")
    (unit / "m365" / "test_client.py").write_text("")

    for name in cs.REQUIRED_ARTIFACTS:
        (tmp_path / name).write_text("# stub\n")
    return tmp_path


def _findings(tree: Path) -> list[str]:
    return [f for group in cs.run(tree).values() for f in group]


def test_clean_tree_passes(clean_tree: Path):
    assert _findings(clean_tree) == []


def test_unexpected_top_level_directory(clean_tree: Path):
    (clean_tree / "src").mkdir()
    assert cs.check_top_level(clean_tree), "an unlisted top-level directory must be a finding"


def test_ignored_directories_are_not_findings(clean_tree: Path):
    (clean_tree / ".github").mkdir()
    (clean_tree / "__pycache__").mkdir()
    assert cs.check_top_level(clean_tree) == []


def test_undeclared_subpackage(clean_tree: Path):
    (clean_tree / cs.PACKAGE / "utils").mkdir()
    (clean_tree / cs.PACKAGE / "utils" / "helpers.py").write_text("X = 1\n")
    assert cs.check_subpackages(clean_tree), "a subpackage with no declared layer must be a finding"


def test_missing_allowed_subpackage_is_fine(clean_tree: Path):
    """Planned-but-absent subpackages must not fail the check.

    `vault/`, `outbox/`, and `parsers/` do not exist until later stages; the
    check rejects the unexpected, never the merely absent.
    """
    assert "vault" in cs.LAYERS
    assert not (clean_tree / cs.PACKAGE / "vault").exists()
    assert cs.check_subpackages(clean_tree) == []


def test_module_over_line_cap(clean_tree: Path):
    body = "\n".join(f"X{n} = {n}" for n in range(cs.MAX_MODULE_LINES + 5))
    (clean_tree / cs.PACKAGE / "index" / "big.py").write_text(body)
    (clean_tree / "tests" / "unit" / "index" / "test_big.py").write_text("")
    assert cs.check_module_size(clean_tree)


def test_module_at_line_cap_passes(clean_tree: Path):
    body = "\n".join(f"X{n} = {n}" for n in range(cs.MAX_MODULE_LINES))
    (clean_tree / cs.PACKAGE / "index" / "exact.py").write_text(body)
    (clean_tree / "tests" / "unit" / "index" / "test_exact.py").write_text("")
    assert cs.check_module_size(clean_tree) == []


def test_sys_path_manipulation(clean_tree: Path):
    (clean_tree / cs.PACKAGE / "config.py").write_text("import sys\nsys.path.insert(0, '.')\n")
    assert cs.check_no_sys_path(clean_tree)


def test_sys_path_in_a_comment_is_not_a_finding(clean_tree: Path):
    (clean_tree / cs.PACKAGE / "config.py").write_text("# never sys.path.append anything\n")
    assert cs.check_no_sys_path(clean_tree) == []


def test_terraform_rejected(clean_tree: Path):
    (clean_tree / "infra").mkdir()
    (clean_tree / "infra" / "main.tf").write_text("resource {}\n")
    assert cs.check_no_terraform(clean_tree)


def test_upward_import(clean_tree: Path):
    """config (L0) importing cli (L5) is the layering inverted."""
    (clean_tree / cs.PACKAGE / "config.py").write_text(f"from {cs.PACKAGE} import cli\n")
    findings = cs.check_import_direction(clean_tree)
    assert findings and "upward" in findings[0]


def test_sideways_import_index_to_m365(clean_tree: Path):
    """The one edge that would make the index depend on the Microsoft half."""
    (clean_tree / cs.PACKAGE / "index" / "sync.py").write_text(
        f"from {cs.PACKAGE}.m365 import client\n"
    )
    findings = cs.check_import_direction(clean_tree)
    assert findings and "sideways" in findings[0]
    assert "index" in findings[0] and "m365" in findings[0]


def test_downward_import_allowed(clean_tree: Path):
    (clean_tree / cs.PACKAGE / "index" / "sync.py").write_text(
        f"from {cs.PACKAGE} import config\nimport {cs.PACKAGE}.model\n"
    )
    assert cs.check_import_direction(clean_tree) == []


def test_relative_import_is_intra_subpackage(clean_tree: Path):
    (clean_tree / cs.PACKAGE / "index" / "sync.py").write_text("from . import __init__\n")
    assert cs.check_import_direction(clean_tree) == []


def test_missing_required_artifact(clean_tree: Path):
    (clean_tree / cs.REQUIRED_ARTIFACTS[0]).unlink()
    assert cs.check_required_artifacts(clean_tree)


def test_module_without_a_test(clean_tree: Path):
    (clean_tree / cs.PACKAGE / "index" / "search.py").write_text("X = 1\n")
    findings = cs.check_tests_exist(clean_tree)
    assert findings and "search.py" in findings[0]


def test_module_outside_test_required_subpackages_needs_no_test(clean_tree: Path):
    (clean_tree / cs.PACKAGE / "schedule.py").write_text("X = 1\n")
    assert cs.check_tests_exist(clean_tree) == []
