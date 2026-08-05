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
import re
import sys
from pathlib import Path

PACKAGE = "m365_brain"

MAX_MODULE_LINES = 300

# subpackage / top-level-module name -> layer. Higher layers may import lower
# layers. A same-layer import is allowed only within one subpackage, which is
# what rejects `index -> m365`: they share a layer deliberately, because
# neither may depend on the other.
#
# The map is also the allow-list. A module that is not in it is a finding --
# adding a subpackage should require naming its layer, not just creating a
# directory.
LAYERS: dict[str, int] = {
    # 0 -- configuration and plain data. Neither imports anything from the package.
    "config": 0,
    "model": 0,
    "models": 0,
    # 1 -- pure functions over layer 0
    "parsers": 1,
    "validation": 1,
    "logging_config": 1,
    "atomic_json": 1,
    # 2 -- persistence primitives
    "storage": 2,
    "state": 2,
    # 3 -- addressing and recording. `manifest` sits here rather than beside
    # `state` because `RecordingStorage` wraps a `StorageBackend`, and a
    # same-layer import between two top-level modules is exactly what this map
    # rejects. `schedule` is here because it reads cursors out of `state`.
    "vault": 3,
    "manifest": 3,
    "schedule": 3,
    # 4 -- the vendor-agnostic write-back machinery: intents, tiers, stores,
    # the runner. It must NOT know that Microsoft 365 exists; the executors
    # that do live in `m365/outboxes/` and import downward into here.
    # `hooks` is here because it hands a `ChangeManifest` to user code.
    "outbox": 4,
    "hooks": 4,
    # 5 -- the two subsystems, peers by construction. Same layer means neither
    # may import the other, which is the point: `index` must stay usable with
    # the Microsoft half absent entirely, and stacking them would block only
    # the direction we happened to think of first.
    "index": 5,
    "m365": 5,
    # 6 -- one pass over layer 5
    "sync": 6,
    "ops": 6,
    "index_step": 6,
    # 7 -- orchestration over passes
    "cycle": 7,
    "worker": 7,
    "dry_run": 7,
    # 8 -- the facade
    "workspace": 8,
    # 9 -- one verb group per module: option parsing, one library call,
    # formatting. Above the facade because it uses it.
    "commands": 9,
    # 10 -- the entry point
    "cli": 10,
}

# Modules that exist today and move under `m365/` during the platform stage.
# They are exempt from the import-direction rule -- not from the allow-list --
# because their current layout predates the layering and rewriting their
# imports twice is churn.
#
# This set is stage M's checklist. When it is empty, the relocation is done,
# and emptying it is a stated acceptance criterion rather than a cleanup
# somebody may or may not get to.
#
# EMPTY as of stage M phase 1: `auth`, `converters`, `extractors`,
# `frontmatter`, `graph_client` (now `m365/client.py`), `graph_helpers` and
# `markdown_writer` all live under `m365/`. Keep the set -- and this comment --
# so a future relocation has somewhere to declare itself.
PENDING_RELOCATION: frozenset[str] = frozenset()

for _name in PENDING_RELOCATION:
    LAYERS.setdefault(_name, LAYERS["m365"])

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
    "site",  # mkdocs build output; gitignored
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
        if path.name == f"test_{Path(__file__).name}":
            continue  # its fixtures are, by construction, the violations

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
        if source_key in PENDING_RELOCATION:
            continue
        source_layer = LAYERS[source_key]
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for target_key in sorted(_imported_keys(tree)):
            if target_key == source_key:
                continue  # intra-subpackage
            if target_key not in LAYERS or target_key in PENDING_RELOCATION:
                continue  # already reported, or awaiting relocation
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


SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
"""Lowercase alphanumerics and single hyphens: the spec's `name` rule.

The regex rejects a leading or trailing hyphen and consecutive hyphens by
construction, which is exactly what the specification requires.
"""

SKILL_NAME_MAX = 64
SKILL_DESCRIPTION_MAX = 1024
SKILL_COMPATIBILITY_MAX = 500


def check_skills(root: Path) -> list[str]:
    """The bundled skills conform to the agentskills.io manifest schema.

    Two of these rules are cheap to get wrong and invisible until install:
    `metadata` values must be *strings*, so an unquoted `version: 1.0` is a
    float and invalid; and `allowed-tools` is a space-separated **string**, not
    a list. Both are checked here rather than trusted to review.
    """
    findings: list[str] = []
    skills = root / "skills"
    if not skills.is_dir():
        return findings

    for directory in sorted(p for p in skills.iterdir() if p.is_dir()):
        manifest = directory / "SKILL.md"
        if not manifest.is_file():
            findings.append(f"skills/{directory.name}/: no SKILL.md")
            continue
        front = _frontmatter(manifest)
        if front is None:
            findings.append(f"skills/{directory.name}/SKILL.md: no YAML frontmatter")
            continue

        name = front.get("name")
        if name != directory.name:
            findings.append(f"skills/{directory.name}/SKILL.md: name is {name!r}, must equal the directory name")
        if not isinstance(name, str) or not SKILL_NAME_RE.match(name) or len(name) > SKILL_NAME_MAX:
            findings.append(f"skills/{directory.name}/SKILL.md: name must be lowercase a-z0-9 with single hyphens")

        description = front.get("description")
        if not isinstance(description, str) or not description or len(description) > SKILL_DESCRIPTION_MAX:
            findings.append(f"skills/{directory.name}/SKILL.md: description must be 1-{SKILL_DESCRIPTION_MAX} chars")

        compatibility = front.get("compatibility")
        if compatibility is not None and len(str(compatibility)) > SKILL_COMPATIBILITY_MAX:
            findings.append(f"skills/{directory.name}/SKILL.md: compatibility exceeds {SKILL_COMPATIBILITY_MAX} chars")

        metadata = front.get("metadata")
        if metadata is not None:
            if not isinstance(metadata, dict):
                findings.append(f"skills/{directory.name}/SKILL.md: metadata must be a map")
            else:
                findings.extend(
                    f"skills/{directory.name}/SKILL.md: metadata.{key} is {type(value).__name__}, must be a string "
                    f"-- quote it"
                    for key, value in metadata.items()
                    if not isinstance(value, str)
                )

        tools = front.get("allowed-tools")
        if tools is not None and not isinstance(tools, str):
            findings.append(
                f"skills/{directory.name}/SKILL.md: allowed-tools must be a space-separated string, not a list"
            )
    return findings


def _frontmatter(path: Path) -> dict | None:
    import yaml

    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return None
    _, _, rest = text.partition("---\n")
    block, marker, _ = rest.partition("\n---\n")
    if not marker:
        return None
    loaded = yaml.safe_load(block)
    return loaded if isinstance(loaded, dict) else None


CHECKS = (
    ("top-level layout", check_top_level),
    ("declared subpackages", check_subpackages),
    ("module size", check_module_size),
    ("no sys.path", check_no_sys_path),
    ("no terraform", check_no_terraform),
    ("import direction", check_import_direction),
    ("required artifacts", check_required_artifacts),
    ("test presence", check_tests_exist),
    ("bundled skills", check_skills),
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
