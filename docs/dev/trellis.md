# Trellis in this repository

Operational notes for the [Trellis](https://www.npmjs.com/package/@mindfoldhq/trellis) workflow
system. Install with `npm install -g @mindfoldhq/trellis@latest`.

| | |
|---|---|
| Version pinned here | **0.6.12** (`trellis --version`, and `.trellis/.version`) |
| Workflow template | **`native`** — the bundled default (`trellis workflow --list`) |
| Platforms enabled | **`--claude` only** |

## Why only `--claude`

`trellis init` offers roughly twenty platform targets (`--cursor`, `--codex`, `--gemini`,
`--droid`, …), and it writes a **separate, unsymlinked copy of every skill per enabled platform**.
The copies are not links to one source, so every problem in the templates multiplies by the
platform count — and so does every hand-fix.

Concretely: the installed templates carry 33 lines of bilingual Chinese content — 30 in
`common/bundled-skills/` (copied per platform) and 3 in `trellis/workflow.md` (written once). One
platform means 33 lines to deal with. Four platforms means 123.

## Mandatory follow-up: `scripts/check_trellis_cjk.py`

**Run it after every `trellis init` and every `trellis update`.** Upstream ships bilingual Chinese
trigger phrases, and `update` restores them.

```bash
python3 scripts/check_trellis_cjk.py          # list offending lines
python3 scripts/check_trellis_cjk.py --quiet  # exit code only
```

The script **detects only — it never edits**. That is deliberate. An auto-stripping predecessor
corrupted files two different ways:

- a DOTALL regex matched from a quote on one line to a quote many lines later and ate everything
  in between;
- a whitespace "tidy" collapsed the space in `python3 ./script.py`, breaking every shell command
  in the file.

The correct transformation is per-file judgement, not a regex:

| File | Action | Why |
|---|---|---|
| `.trellis/workflow.md` | **delete** the Chinese | it is redundant with an English phrase already in the same sentence |
| skill trigger tables / phrase lists | **translate**, keeping the English original | the Chinese phrases have no English counterpart, so deleting them narrows skill activation |

## Recovery

If an edit goes wrong, `trellis update --force` rewrites every managed file from the installed
package. They are upstream templates, so nothing local is lost — except the trap below.

## Trap: `trellis update --force` drops local keys from `.claude/settings.json`

It silently rewrites that file from the template. **Re-check `.claude/settings.json` after any
update** and restore anything this repo added on top of the Trellis hooks — today that is the
`env` block (`CLAUDE_BASH_MAINTAIN_PROJECT_WORKING_DIR`).

Then re-run `python3 scripts/check_trellis_cjk.py`, because `--force` restores the Chinese too.
