#!/usr/bin/env python3
"""Report bilingual Chinese content in Trellis-managed files.

Upstream Trellis (@mindfoldhq/trellis) ships bilingual prompts: skill trigger
phrases, workflow examples, and frontmatter descriptions carry Chinese
alternates alongside the English. Both `trellis init` and `trellis update`
restore them, so this runs as the mandatory follow-up to either.

    python3 scripts/check_trellis_cjk.py          # list offending lines
    python3 scripts/check_trellis_cjk.py --quiet  # exit code only

Exit 0 when clean, 1 when Chinese content is present.

DETECT ONLY -- it deliberately does not edit. An earlier version tried to strip
automatically and corrupted the files: the transformation is not mechanical.
The established convention differs per file, and only a reader can tell which
applies:

  * `.trellis/workflow.md` -- DELETE the Chinese alternates. They are redundant
    with an English phrase already in the same sentence
    (`Reply 'ok' / '行'` -> `Reply 'ok'`).
  * skill trigger tables and phrase lists -- TRANSLATE. The Chinese phrases are
    distinct trigger phrases with no English counterpart, so deleting them
    narrows skill activation. `"和 codex/claude 讨论一下"` became
    `"discuss with codex/claude"`, kept alongside the English original.

Two further traps a regex hits:

  * Quoted Chinese spans wrap across newlines, so a DOTALL match happily eats
    every line between two distant quotes.
  * Whitespace "tidying" after a deletion mangles unrelated text -- collapsing
    the space in `python3 ./script.py` breaks every shell command in the file.

Recovery, if an edit does go wrong: `trellis update --force` rewrites every
managed file from the installed package, since these are upstream templates.

`.trellis/tasks/` is never scanned -- those are user-authored documents and may
legitimately quote Chinese.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# CJK Unified Ideographs plus the fullwidth punctuation that travels with them.
HAS_CJK = re.compile("[一-鿿　-〿＀-￯]")

# `trellis init` writes skills into a per-platform directory, and a repo may
# have several enabled at once. Glob every platform Trellis supports rather
# than the ones this workspace happens to use -- a missed directory reads as
# "clean" when it is merely unscanned.
TARGET_GLOBS = (
    ".trellis/*.md",
    ".trellis/agents/*.md",
    "agents/trellis-*.md",
    *(
        f"{platform}/{sub}/trellis*/**/*.md"
        for platform in (
            ".agents", ".claude", ".cursor", ".codex", ".opencode", ".kilocode",
            ".kiro", ".gemini", ".qoder", ".codebuddy", ".droid", ".pi", ".omp",
            ".zcode", ".reasonix", ".trae", ".antigravity", ".devin", ".github",
        )
        for sub in ("skills", "commands", "agents", "workflows", "extensions")
    ),
)

EXCLUDE_PARTS = (".trellis/tasks", ".trellis/workspace", ".trellis/.runtime")


def targets(root: Path) -> list[Path]:
    """Trellis-managed markdown, deduplicated by resolved path.

    Platform directories are often symlinks onto one source tree, so the same
    file surfaces under several globs; resolving collapses them. `.cursor/` is
    a real copy rather than a symlink, so it is reported separately -- fixes
    have to be mirrored there by hand.
    """
    seen: dict[Path, Path] = {}
    for pattern in TARGET_GLOBS:
        for path in root.glob(pattern):
            if not path.is_file():
                continue
            if any(part in path.as_posix() for part in EXCLUDE_PARTS):
                continue
            seen.setdefault(path.resolve(), path)
    return sorted(seen.values())


def main() -> int:
    parser = argparse.ArgumentParser(description="Report Chinese content in Trellis files.")
    parser.add_argument("--quiet", action="store_true", help="exit code only, no output")
    parser.add_argument("--root", default=".", help="repo root (default: cwd)")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    files = targets(root)
    if not files:
        print(f"no Trellis-managed files found under {root}", file=sys.stderr)
        return 1

    hits = 0
    for path in files:
        rel = path.relative_to(root)
        for number, line in enumerate(path.read_text(encoding="utf-8").split("\n"), start=1):
            if not HAS_CJK.search(line):
                continue
            hits += 1
            if not args.quiet:
                print(f"{rel}:{number}: {line.strip()[:160]}")

    if hits and not args.quiet:
        print(f"\n{hits} line(s) need translating or deleting by hand -- see this file's docstring.")
    elif not hits and not args.quiet:
        print(f"clean: {len(files)} Trellis files, no Chinese content")
    return 1 if hits else 0


if __name__ == "__main__":
    raise SystemExit(main())
