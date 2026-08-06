---
name: m365-brain-knowledge
description: Search and navigate a markdown knowledge index — full-text, semantic and hybrid search, entity graph traversal, and recent-activity listing. Use when looking up stored information, following relationships between entities, or listing what changed recently in an indexed markdown collection.
license: MIT
compatibility: Requires the m365-brain CLI on PATH and the M365_BRAIN_CONFIG environment variable set to a config file path. Semantic and hybrid search additionally require index.vector.enabled in that config.
allowed-tools: Bash(m365-brain:*) Read Glob
metadata:
  version: "1.0"
  category: "knowledge"
  homepage: "https://github.com/neuralsignal/m365-brain"
---

# Knowledge search

Every command is `m365-brain --config "$M365_BRAIN_CONFIG" …`.

**Results go to stdout. Logs go to stderr.** Pass `--json` whenever you intend
to parse the output; you never need to strip log lines first.

Exit codes: `0` success · `1` an operation failed · `2` bad command line ·
`3` configuration invalid or a name that does not resolve · `4` re-authenticate.
A `3` means fix the config or the name — retrying will not help.

## Search

    m365-brain --config "$M365_BRAIN_CONFIG" index search "quarterly review" --json
    m365-brain --config "$M365_BRAIN_CONFIG" index search "budget" --search-type hybrid --json
    m365-brain --config "$M365_BRAIN_CONFIG" index search "onboarding" --type note --tag process --json
    m365-brain --config "$M365_BRAIN_CONFIG" index search "roadmap" --field status=open --json
    m365-brain --config "$M365_BRAIN_CONFIG" index search "roadmap" --limit 5 --page 2 --json

`--search-type` is `text` (default), `vector` or `hybrid`. Vector and hybrid
need `index.vector.enabled: true`; with it off they raise naming the key rather
than silently falling back to full text — so a result set is always the ranking
that was asked for.

Query syntax (operators, field filters, timeframes): `references/search-syntax.md`.

JSON shape:

```json
{"total": 12, "returned": 5, "limit": 5, "page": 2,
 "results": [{"permalink": "...", "title": "...", "type": "...",
              "file_path": "...", "updated_at": "...", "score": 1.4, "snippet": "..."}]}
```

`returned < total` means the answer was cut short by `limit` — raise it or ask
for the next `--page`. Every verb taking a `--limit` carries the same three
keys, so one check covers all of them.

## Follow relationships

    m365-brain --config "$M365_BRAIN_CONFIG" index context "Project Atlas" --depth 2 --format json
    m365-brain --config "$M365_BRAIN_CONFIG" index context --permalink project-atlas --depth 1 --format json

Give either the entity name as an argument or `--permalink`, never both. The
result carries the entity, its observations, and every edge within `--depth`
hops with the depth and direction each was found at.

## What changed recently

    m365-brain --config "$M365_BRAIN_CONFIG" index recent --timeframe 7d --json
    m365-brain --config "$M365_BRAIN_CONFIG" index recent --timeframe "2 weeks ago" --type meeting --json

## Keep the index current

    m365-brain --config "$M365_BRAIN_CONFIG" index sync
    m365-brain --config "$M365_BRAIN_CONFIG" index sync --root notes
    m365-brain --config "$M365_BRAIN_CONFIG" index rebuild --yes

`sync` is checksum-driven and cheap; `rebuild` reparses everything and needs
`--yes` because it is not. A file that has gone is pruned on the next sync,
so a stale hit means a sync is due, not that the index is wrong.

## Where things live

    m365-brain --config "$M365_BRAIN_CONFIG" index paths --json

Prints the configured roots, the database path, and the directory each
extractor writes into. **Read this rather than assuming a folder layout** —
every directory name is a config value and none of them is guaranteed.

## Notes on results

- `permalink` is the stable identifier. `file_path` moves when a file moves.
- A search that returns nothing is a search that returned nothing. Retry with
  `--search-type hybrid` before concluding the information is absent: full-text
  search is exact-token, and a paraphrase or a misspelling will miss.
- Note structure — frontmatter, observations, relations — is described in
  `references/note-format.md`. The vocabulary inside those structures belongs
  to whoever writes the notes, not to this skill.
