#!/usr/bin/env python3
"""Reject references to the private workspace this package was extracted from.

    python3 scripts/check_no_workspace.py           # list every finding
    python3 scripts/check_no_workspace.py --quiet   # exit code only

Exit 0 when clean, 1 otherwise.

This package absorbed code from a private workspace whose vocabulary -- its
name, its packages, its folder contract -- was baked into everything it
touched. A public library carrying one user's directory names is not a
library; it is that user's script with a `pyproject.toml`.

The check is a ratchet, not a cleanup: the repo was already clean when this
landed, and every later stage folds in more of that workspace's code. Running
from the first commit is the whole point -- retrofitting after the fact turns
a grep into archaeology.

Note the lookbehind on `brain`: this package is named `m365_brain`, so a
substring match would reject its own name on every line. A plain `\\bbrain\\b`
is not enough either -- `-` is a non-word character, so it puts a word boundary
right before the `brain` in `m365-brain`. `(?<![\\w-])brain\\b` matches `Brain`
and `brain` but not `m365_brain`, `m365-brain`, `brainstorm`, or `no-brainer`.

The same lookbehind is on the packages-and-skills pattern, and for the same
reason: this repo ships skills called `m365-brain-knowledge`, `-files` and
`-ops`, whose names are deliberately prefixed so they do not squat a bare
`knowledge` or `files` in a flat installed tree. Without the lookbehind the
check would reject the very naming decision it exists to protect.
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

# (compiled pattern, what it catches) -- the message is the finding's whole
# explanation, so write it for someone who has never seen the old workspace.
PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?<![\w-])brain\b", re.IGNORECASE), "the consuming workspace's name"),
    (re.compile(r"(?<![\w-])brain[-_](db|knowledge|files|ops)\b", re.IGNORECASE), "its packages and skills"),
    (re.compile(r"agentic[-_]brain\b", re.IGNORECASE), "its retired design package"),
    (re.compile(r"\.brain/"), "its state directory"),
    (re.compile(r"knowledge/(notes|people|goals|tasks)\b"), "its folder contract"),
    (re.compile(r"m365[-_]data[-_]sync\b", re.IGNORECASE), "the app absorbed into this package"),
    (re.compile(r"sanoptis[-_]sources\b", re.IGNORECASE), "the repo retired into this package"),
)

# Exemptions live here rather than in the invocation, so the exempt set is
# reviewable in the same place as the rules.
#
#   CHANGELOG.md      release-please owns it; release history is immutable
#   .trellis/tasks/   task documents legitimately discuss the consumer
#   this file         the pattern table necessarily spells out what it rejects
#   its test file     its fixtures are, by construction, the leaks themselves
#   independence_check.sh  it runs this same check by hand against a scratch
#                     directory, so it has to spell the vocabulary too
SCRIPT_NAME = Path(__file__).name
TEST_NAME = f"test_{SCRIPT_NAME}"
EXEMPT_FILES = {"CHANGELOG.md", SCRIPT_NAME, TEST_NAME, "independence_check.sh"}
EXEMPT_DIR_PARTS = (
    ".git",
    ".pixi",
    ".ruff_cache",
    ".pytest_cache",
    ".hypothesis",
    "node_modules",
    "__pycache__",
    ".web",
    ".trellis/tasks",
    ".trellis/workspace",
)

SCANNED_SUFFIXES = {
    ".py", ".md", ".toml", ".yaml", ".yml", ".json", ".bicep", ".bicepparam",
    ".sh", ".cfg", ".ini", ".txt", ".example", ".Dockerfile",
}
SCANNED_NAMES = {"Dockerfile", ".env.example", ".dockerignore", ".gitignore"}

# Lock files are generated, enormous, and may legitimately contain a path
# fragment from whoever generated them.
EXEMPT_NAMES = {"pixi.lock", "uv.lock", "poetry.lock", "package-lock.json"}


def scannable(path: Path, root: Path) -> bool:
    if not path.is_file():
        return False
    posix = path.relative_to(root).as_posix()
    if any(part in posix for part in EXEMPT_DIR_PARTS):
        return False
    if path.name in EXEMPT_FILES or path.name in EXEMPT_NAMES:
        return False
    return path.suffix in SCANNED_SUFFIXES or path.name in SCANNED_NAMES


def scan(root: Path) -> list[str]:
    findings = []
    for path in sorted(root.rglob("*")):
        if not scannable(path, root):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for number, line in enumerate(text.splitlines(), start=1):
            for pattern, why in PATTERNS:
                if pattern.search(line):
                    rel = path.relative_to(root)
                    findings.append(f"{rel}:{number}: {why} -- {line.strip()[:120]}")
                    break
    return findings


def main() -> int:
    parser = argparse.ArgumentParser(description="Reject consuming-workspace references.")
    parser.add_argument("--quiet", action="store_true", help="exit code only")
    parser.add_argument("--root", default=".", help="repo root (default: cwd)")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    findings = scan(root)

    if not args.quiet:
        for finding in findings:
            print(finding)
        print(
            f"\n{len(findings)} reference(s) to the consuming workspace"
            if findings
            else "clean: no consuming-workspace references"
        )
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
