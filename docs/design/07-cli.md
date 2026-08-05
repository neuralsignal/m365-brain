---
title: "Design: CLI Design"
status: draft
date: 2026-03-08
author: Matthias Christenson
---

# CLI Design

## Context

The consuming workspace used loose Python scripts (`search.py`, `build_context.py`, `extract.py`) invoked via pixi. This document consolidates them into a single CLI installed as a console script via pip.

In `m365_brain` the console script is `m365-brain`, with `mb` as a short alias. The verbs below are the knowledge-layer half of the target verb set recorded in `CONTRACTS.md`; the Microsoft 365 half adds `auth`, `run`, `outbox`, `files`, `teams`, and `status`. Knowledge verbs are grouped under `index` there — `index sync`, `index search`, `index context`, `index recent` — because the noun disambiguates them from the Microsoft 365 sync.

## Scope for v0.1

**In scope:**

- Command structure: `m365-brain <command> [options]`
- Commands: `init`, `index sync`, `index search`, `index context`, `extract`, `catalog`, `validate`, `index recent`, `connector`
- Configuration loading: the config file, env var overrides
- Output formats: text (default), JSON, markdown
- Exit codes

**Out of scope:**

- Interactive TUI or REPL
- Watch mode (auto-sync on file change)
- Shell completions (deferred to v0.2)

## Design

### Command Overview

| Command | Purpose | Reads DB | Writes DB |
|---------|---------|----------|-----------|
| `init` | Scaffold a new workspace | No | No |
| `index sync` | Index markdown files into SQLite | Yes | Yes |
| `index search` | Full-text, vector, or hybrid search | Yes | No |
| `index context` | Graph traversal from a seed entity | Yes | No |
| `extract` | Convert a file to markdown | Yes | Yes |
| `catalog` | Browse and manage the file catalog | Yes | Yes |
| `validate` | Check markdown files against schemas | Yes | No |
| `index recent` | Show recently modified entities | Yes | No |
| `connector` | Run file discovery from connectors | Yes | Yes |

### `init`

Scaffold a new knowledge workspace.

```
m365-brain init [--dir PATH] [--minimal]
```

Actions:

1. Create the config file with all config values explicitly set
2. Create the vault root with conventional subdirectories (`people/`, `goals/`, `tasks/`, `notes/`)
3. Create the meta directory (`_meta/`) for the index database
4. Copy built-in schemas into the workspace (unless `--minimal`)
5. Copy built-in templates into the workspace (unless `--minimal`)
6. Add the meta directory to `.gitignore`

Every one of those directory names is read from the config being written, not hardcoded.

If the config file already exists, error with: "Workspace already initialized. Use --force to reinitialize."

### `index sync`

Index markdown files into the SQLite database.

```
m365-brain index sync [--full-rebuild] [--rebuild-vectors] [--verbose]
```

Options:

- `--full-rebuild` — Clear and rebuild everything from scratch
- `--rebuild-vectors` — Rebuild only vector embeddings (requires `[vector]`)
- `--verbose` — Show per-file sync status

Behavior:

1. Load config
2. Initialize database tables (if first run)
3. Scan the configured index roots for `.md` files
4. Incremental sync: skip unchanged files (by checksum)
5. Parse changed/new files, upsert entities + observations + relations
6. Remove entries for deleted files
7. Update FTS5 index
8. If vector enabled: embed new/changed chunks
9. Report: N entities synced, N unchanged, N removed

Source: the retired index library, `__init__.py:1042-1197`

### `index search`

Search the knowledge base.

```
m365-brain index search <query> [options]
```

Options:

- `--type TYPE` — Filter by entity type
- `--tag TAG` — Filter by tag
- `--field KEY=VALUE` — Metadata filter (supports `=`, `>`, `>=`, `<`, `<=`, `~` (IN), `:` (BETWEEN))
- `--search-type {text,vector,hybrid}` — Search mode (default: text)
- `--page N` — Page number (default: 1)
- `--page-size N` — Results per page (default: 20)
- `--vector-k N` — Vector candidate count (default: 100)
- `--min-similarity F` — Minimum similarity for vector results (default: 0.55)
- `--include-files` — Also search file catalog
- `--format {text,json,markdown}` — Output format
- `--all` — List all entities (no query needed)

Every "default" above is a config value the CLI reads, not a literal in a function signature.

Output (text mode):

```
1. [person] John Doe
   path: people/john-doe.md
   permalink: people/john-doe
   tags: person, engineering
   snippet: ...senior >>>engineer<<< at Acme...

Page 1 of 3 (47 total)
```

Source: the consuming workspace's knowledge-search script

### `index context`

Navigate the knowledge graph via entity lookup and relation traversal.

```
m365-brain index context <entity> [options]
m365-brain index context --permalink <permalink> [options]
m365-brain index context --pattern "people/*" [options]
```

Options:

- `--permalink PERMALINK` — Look up by permalink instead of title
- `--depth N` — Traversal depth (default: 1)
- `--pattern GLOB` — Match multiple entities by permalink glob
- `--format {text,json,markdown,compact}` — Output format

Source: the consuming workspace's context-building script

### `extract`

Convert a file to markdown and update the catalog. Delegates to `obsidian-import` for the actual extraction (requires the `[convert]` extra).

```
m365-brain extract <path>
m365-brain extract --search <query>
m365-brain extract --id <catalog-id>
m365-brain extract --all-pending [--limit N]
m365-brain extract --force           # re-extract even if already converted
```

Options:

- `<path>` — Extract a specific file by path
- `--search QUERY` — Search catalog by filename, extract first match
- `--id ID` — Extract by catalog entry ID
- `--all-pending` — Batch convert all pending files
- `--limit N` — Max files for `--all-pending` (default: 10)
- `--force` — Re-extract even if already converted
- `--quiet` — Suppress extracted text output
- `--format {text,json}` — Output format

Delegates to `obsidian-import` (see `04-file-extraction.md`). Source pattern: the consuming workspace's file skill, `scripts/extract.py`

### `catalog`

Browse and manage the file catalog.

```
m365-brain catalog [options]
m365-brain catalog search <query>
m365-brain catalog stats
```

Subcommands:

- `catalog` — List recent catalog entries
- `catalog search <query>` — Search by filename
- `catalog stats` — Show conversion status summary
- `catalog --source SOURCE` — Filter by connector source
- `catalog --extension EXT` — Filter by file extension
- `catalog --status STATUS` — Filter by conversion status

### `validate`

Check markdown files against their schemas.

```
m365-brain validate [--type TYPE] [--strict] [paths...]
```

Options:

- `--type TYPE` — Validate only files of this type
- `--strict` — Treat warnings as errors
- `paths...` — Specific files to validate (default: all)

Checks:

1. Frontmatter structure (required fields present)
2. Required observations (per schema)
3. Relation target resolution (warn on unresolved)
4. Observation syntax validity

### `index recent`

Show recently modified entities.

```
m365-brain index recent [--within TIMEFRAME] [--type TYPE] [--format {text,json}]
```

Timeframe syntax: `7d`, `2w`, `1m`, `today`, `yesterday`, `last week`

Source: the retired index library, `__init__.py:584-630` (timeframe parser)

### `connector`

Run file discovery from configured connectors.

```
m365-brain connector discover [--name NAME] [--dry-run]
m365-brain connector list
```

Subcommands:

- `discover` — Run discovery for all enabled connectors (or a specific one)
- `list` — Show registered connectors and their status
- `--dry-run` — Show what would be cataloged without writing

### Configuration Loading

1. Check `--config PATH` CLI option (explicit path; comma-separated paths deep-merge left to right)
2. Check the `M365_BRAIN_CONFIG` environment variable
3. Walk up from CWD looking for the config file
4. If not found: error (except for `init`)

Environment variable overrides follow the pattern `M365_BRAIN_<SECTION>_<KEY>`:

- `M365_BRAIN_DATABASE_PATH` → `database_path`
- `M365_BRAIN_VECTOR_MODEL` → `index.vector.model`
- `M365_BRAIN_BUSY_TIMEOUT_MS` → `busy_timeout_ms`

Separately, and already implemented, any string value in the config file may contain `${VAR}`, expanded from the environment at load time; a missing variable raises rather than falling back.

### Output Formats

All commands support `--format` with these modes:

| Format | Description | Use Case |
|--------|-------------|----------|
| `text` | Human-readable plain text | Interactive terminal use |
| `json` | Structured JSON | Piping to jq, agent consumption |
| `markdown` | Formatted markdown | Agent context windows, docs |
| `compact` | Minimal JSON (no whitespace) | Programmatic parsing |

### Exit Codes

| Code | Meaning |
|------|---------|
| 0 | Success |
| 1 | General error |
| 2 | Configuration error (missing config file, invalid config) |
| 3 | Database error (missing DB, schema mismatch) |
| 4 | Validation error (schema violations in strict mode) |

## Decisions

### D-20: CLI Framework Choice

- **Context:** Need a CLI framework.
- **Options:**
  - A) Click — mature, widely used, explicit
  - B) Typer — builds on Click, uses type hints for auto-generation
  - C) argparse — stdlib, no dependency
- **Chosen:** A) Click
- **Consequences:** Click is the most widely used Python CLI framework. It's explicit (no magic type hint inference), well-documented, and supports nested commands natively. Typer adds convenience but also adds a dependency on Click anyway. argparse is verbose for nested command structures. Already realised: `m365_brain/cli.py` is Click.

### D-21: Config File Discovery Strategy

- **Context:** How does the CLI find the config file?
- **Options:**
  - A) CWD only — must run from workspace root
  - B) Walk up directory tree — like git finds `.git`
  - C) Explicit path only — `--config` required
  - D) XDG config directory
- **Chosen:** B) Walk up directory tree
- **Consequences:** Matches user expectations from git, npm, cargo. Users can run a search from any subdirectory of their workspace. The workspace root is the directory containing the config file. `--config` overrides the walk-up search, and `M365_BRAIN_CONFIG` overrides it too. Note the interaction with a rule already implemented: relative path keys resolve against the *config file's* directory, never the process working directory — which is what makes the walk-up safe.

## Open Questions

1. **Shell completions** — Click supports bash/zsh/fish completions. Should we ship them in v0.1 or defer?
2. **Progress bars** — For `index sync` and `extract --all-pending`, should we use rich/tqdm for progress? Or keep it simple with print statements?
3. **Color output** — Should we use colored output? Click supports it. Disable when piped (non-TTY).
4. **A `watch` verb** — File watching for auto-sync is a common request. Defer to v0.2 with watchdog or inotify.

## References

- Search CLI: the consuming workspace's knowledge-search script
- Context CLI: the consuming workspace's context-building script
- Extract CLI: the consuming workspace's file skill, `scripts/extract.py`
- Click docs: <https://click.palletsprojects.com/>
- Timeframe parser: the retired index library, `__init__.py:584-630`
- `CONTRACTS.md` § CLI — the full target verb set across both halves
