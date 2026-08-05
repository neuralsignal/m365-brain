#!/usr/bin/env python3
"""Enforce the repository's structural rules.

    python3 scripts/check_structure.py           # list every finding
    python3 scripts/check_structure.py --quiet   # exit code only

Exit 0 when clean, 1 otherwise.

The rules exist because this package absorbs code from three other codebases.
Each one guards against a specific way that absorption goes wrong:

  * an unexpected top-level directory      -> the repo grows a second home for
    the same concern
  * a module over MAX_MODULE_LINES         -> a monolith gets relocated instead
    of decomposed
  * an upward or sideways import           -> the layering that makes the index
    usable without the Microsoft 365 half quietly disappears
  * a module with no test file             -> code lands untested
  * `sys.path` manipulation or Terraform   -> both are settled decisions

The layer map below is the single source of truth for BOTH which subpackages
may exist and how they may import each other. A module that is not in the map
is a finding: adding a subpackage is a deliberate act, so it should require
naming its layer.
"""

from __future__ import annotations

import argparse
import ast
import sys
from pathlib import Path

PACKAGE = "m365_brain"

MAX_MODULE_LINES = 300

# subpackage / top-level-module name -> layer. Higher layers may import lower
# layers. A same-layer import is allowed only within one subpackage, which is
# what rejects `index -> m365`: they share layer 2 deliberately, because
# neither may depend on the other.
LAYERS: dict[str, int] = {
    # layer 0 -- pure data and configuration, no I/O beyond reading its own input
    "config": 0,
    "model": 0,
    "models": 0,
    "parsers": 0,
    "storage": 0,
    "logging_config": 0,
    "validation": 0,
    # layer 1 -- persisted bookkeeping
    "state": 1,
    "manifest": 1,
    # layer 2 -- the two halves that must not know about each other
    "index": 2,
    "vault": 2,
    "outbox": 2,
    "m365": 2,
    # layer 3 -- orchestration over layer 2
    "schedule": 3,
    "hooks": 3,
    "dry_run": 3,
    "sync": 3,
    "worker": 3,
    # layer 4 -- the facade
    "workspace": 4,
    # layer 5 -- the entry point
    "cli": 5,
}

ALLOWED_TOP_LEVEL_DIRS = {
    PACKAGE,
    "m365_admin",
    "alembic",
    "config",
    "docs",
    "infra",
    "scripts",
    "skills",
    "tests",
}

# Directories that tooling owns. Never a finding, never scanned.
IGNORED_TOP_LEVEL_DIRS = {
    ".git",
    ".github",
    ".claude",
    ".agents",
    ".trellis",
    ".pixi",
    ".ruff_cache",
    ".pytest_cache",
    ".hypothesis",
    "node_modules",
    "__pycache__",
    ".web",
    "assets",
}

REQUIRED_ARTIFACTS = ("INTENT.md", "CONTRACTS.md")

# Subpackages whose every module must have a matching test file. These are the
# ones carrying ported logic, where an untested module is a real risk.
TEST_REQUIRED_SUBPACKAGES = ("index", "outbox", "vault", "m365")


def _python_files(root: Path) -> list[Path]:
    return sorted(
        p
        for p in (root / PACKAGE).rglob("*.py")
        if "__pycache__" not in p.parts
    )


def _module_key(path: Path, root: Path) -> str:
    """The layer-map key for a module: its subpackage, or its own stem."""
    rel = path.relative_to(root / PACKAGE)
    return rel.parts[0] if len(rel.parts) > 1 else rel.stem


def check_top_level(root: Path) -> list[str]:
    findings = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        name = entry.name
        if name in IGNORED_TOP_LEVEL_DIRS or name in ALLOWED_TOP_LEVEL_DIRS:
            continue
        findings.append(f"{name}/: unexpected top-level directory")
    return findings


def check_subpackages(root: Path) -> list[str]:
    """Every subpackage and top-level module must declare a layer.

    A missing entry from LAYERS is fine -- planned subpackages do not exist
    yet. An *undeclared* one is not.
    """
    findings = []
    for path in _python_files(root):
        key = _module_key(path, root)
        if key == "__init__":
            continue
        if key not in LAYERS:
            findings.append(
                f"{path.relative_to(root)}: '{key}' is not in LAYERS -- "
                f"declare its layer before adding it"
            )
    return findings


def check_module_size(root: Path) -> list[str]:
    findings = []
    for path in _python_files(root):
        lines = len(path.read_text(encoding="utf-8").splitlines())
        if lines > MAX_MODULE_LINES:
            findings.append(
                f"{path.relative_to(root)}: {lines} lines > {MAX_MODULE_LINES} -- decompose it"
            )
    return findings


def check_no_sys_path(root: Path) -> list[str]:
    findings = []
    for path in _python_files(root) + sorted((root / "tests").rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if "sys.path.append" in stripped or "sys.path.insert" in stripped:
                findings.append(f"{path.relative_to(root)}:{number}: sys.path manipulation")
    return findings


def check_no_terraform(root: Path) -> list[str]:
    findings = []
    for suffix in ("*.tf", "*.tfvars"):
        for path in root.rglob(suffix):
            if any(part in IGNORED_TOP_LEVEL_DIRS for part in path.parts):
                continue
            findings.append(f"{path.relative_to(root)}: Terraform -- this repo uses Bicep")
    return findings


def _imported_keys(tree: ast.AST) -> set[str]:
    """First-segment package-internal import targets, e.g. {'index', 'config'}."""
    keys: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                parts = alias.name.split(".")
                if parts[0] == PACKAGE and len(parts) > 1:
                    keys.add(parts[1])
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # relative import -- stays within its own subpackage
                continue
            if node.module is None:
                continue
            parts = node.module.split(".")
            if parts[0] != PACKAGE:
                continue
            if len(parts) > 1:
                keys.add(parts[1])
            else:
                # `from m365_brain import cli, config` -- the subpackage names
                # are the imported aliases, not part of the module path.
                keys.update(alias.name for alias in node.names)
    return keys


def check_import_direction(root: Path) -> list[str]:
    findings = []
    for path in _python_files(root):
        source_key = _module_key(path, root)
        if source_key not in LAYERS:
            continue  # already reported by check_subpackages
        source_layer = LAYERS[source_key]
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for target_key in sorted(_imported_keys(tree)):
            if target_key == source_key:
                continue  # intra-subpackage
            if target_key not in LAYERS:
                continue  # already reported
            target_layer = LAYERS[target_key]
            if target_layer < source_layer:
                continue
            direction = "upward" if target_layer > source_layer else "sideways"
            findings.append(
                f"{path.relative_to(root)}: {direction} import "
                f"{source_key}(L{source_layer}) -> {target_key}(L{target_layer})"
            )
    return findings


def check_required_artifacts(root: Path) -> list[str]:
    return [f"{name}: missing" for name in REQUIRED_ARTIFACTS if not (root / name).is_file()]


def check_tests_exist(root: Path) -> list[str]:
    """Every module in a test-required subpackage has a matching test file.

    Presence, not coverage. It catches the module that shipped with no test at
    all, which is the failure a structure check can actually see.
    """
    findings = []
    unit = root / "tests" / "unit"
    for path in _python_files(root):
        rel = path.relative_to(root / PACKAGE)
        if not rel.parts or rel.parts[0] not in TEST_REQUIRED_SUBPACKAGES:
            continue
        if path.stem == "__init__":
            continue
        expected = unit.joinpath(*rel.parts[:-1], f"test_{path.stem}.py")
        if not expected.is_file():
            findings.append(
                f"{rel}: no test at {expected.relative_to(root)}"
            )
    return findings


CHECKS = (
    ("top-level layout", check_top_level),
    ("declared subpackages", check_subpackages),
    ("module size", check_module_size),
    ("no sys.path", check_no_sys_path),
    ("no terraform", check_no_terraform),
    ("import direction", check_import_direction),
    ("required artifacts", check_required_artifacts),
    ("test presence", check_tests_exist),
)


def run(root: Path) -> dict[str, list[str]]:
    return {name: check(root) for name, check in CHECKS}


def main() -> int:
    parser = argparse.ArgumentParser(description="Enforce repository structure rules.")
    parser.add_argument("--quiet", action="store_true", help="exit code only")
    parser.add_argument("--root", default=".", help="repo root (default: cwd)")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    if not (root / PACKAGE).is_dir():
        print(f"{PACKAGE}/ not found under {root}", file=sys.stderr)
        return 1

    results = run(root)
    total = sum(len(v) for v in results.values())

    if not args.quiet:
        for name, findings in results.items():
            for finding in findings:
                print(f"[{name}] {finding}")
        print(
            f"\n{total} finding(s)" if total else "clean: structure rules satisfied"
        )
    return 1 if total else 0


if __name__ == "__main__":
    raise SystemExit(main())
