---
title: "Design: Knowledge Model Specification"
status: draft
date: 2026-03-08
author: Matthias Christenson
---

# Knowledge Model Specification

## Context

The consuming workspace uses a markdown-based entity/observation/relation model as its knowledge representation. Files are the source of truth; the SQLite database is a derived index. This document formalizes the format as a specification that the knowledge layer implements and that third-party tools can parse independently.

The format is inspired by [basic-memory](https://github.com/basicmachines-co/basic-memory) and evolved through production use with AI agents that read, search, and update the knowledge base daily.

## Scope for v0.1

**In scope:**

- Formal specification of document structure (frontmatter, observations, relations)
- Observation syntax: parsing rules, regex patterns, edge cases
- Relation syntax: explicit and inline wikilinks
- Frontmatter schema: YAML structure, structural keys, promoted fields
- Schema system: YAML schema files for entity type validation
- Template system: scaffolding for new entities
- Built-in entity types: person, project, goal, task, note, learning, memory, rule, memo, calendar_event

**Out of scope:**

- How the model maps to SQL (see `05-database-search.md`)
- File discovery and extraction (see `04-file-extraction.md`, `06-connector-protocol.md`)
- CLI commands for creating entities (see `07-cli.md`)

## Design

### Document Structure

Every knowledge document is a plain UTF-8 Markdown file with three parts:

```
┌─────────────────────────┐
│  YAML Frontmatter       │  --- delimited
├─────────────────────────┤
│  Content Body           │  Markdown prose + observations
│  - [category] content   │
├─────────────────────────┤
│  Relations              │  Wikilink-based connections
│  - rel_type [[Target]]  │
└─────────────────────────┘
```

The `## Observations` and `## Relations` headings are conventional but not required. The parser detects observations and relations by their syntax patterns anywhere in the document body.

### Frontmatter Specification

YAML metadata between `---` fences at the top of the file.

#### Structural Fields

| Field | Type | Required | Fallback | Description |
|-------|------|----------|----------|-------------|
| `title` | string | No | filename stem | Display name and primary lookup key |
| `type` | string | No | `"note"` | Entity type for filtering, schema selection |
| `tags` | list[string] | No | `[]` | Classification tags for search |
| `permalink` | string | No | slugified title | Stable identifier, used for linking |
| `aliases` | list[string] | No | `[]` | Alternative names for wikilink resolution |

The set of structural keys that are NOT promoted to observations:

```python
STRUCTURAL_FM_KEYS = frozenset({
    "title", "type", "permalink", "tags", "aliases",
    "source", "annotations", "original_path",
    "participants", "last_message_time",
    "action", "to", "cc", "bcc", "subject", "attachments",
    "in_reply_to", "outlook_entry_id", "original_email",
    "created_at", "sent_at", "error",
})
```

Source: the retired index library, `__init__.py:642-650`

#### Promoted Properties

Scalar frontmatter keys NOT in `STRUCTURAL_FM_KEYS` are automatically promoted to synthetic observations during indexing. This allows Obsidian-native frontmatter properties to be searchable alongside body observations.

Promotion rules:

- Only scalar values (str, int, float, bool, date) are promoted
- Lists and dicts are skipped
- If a body observation has the same category (case-insensitive), the body observation takes precedence
- Dates are serialized as ISO strings; numbers and booleans are stringified

Per-type promoted fields (recommended for frontmatter):

| Type | Promoted Fields |
|------|----------------|
| `person` | `email`, `company`, `role`, `tier`, `last_interaction`, `preferred_language` |
| `goal` | `status`, `timeframe`, `priority` |
| `task` | `status`, `due_date`, `priority`, `completed_at` |
| `project` | `status`, `started`, `tech_stack` |
| `calendar_event` | `start`, `end`, `location`, `organizer` |
| `memory` | `event_type` |
| `learning` | (none beyond standard) |
| `rule` | (none beyond standard) |

Source: the bundled knowledge skill's `references/note-format.md:47-58`

The type vocabulary above is a *recommended* set, not policy: which note types an operator uses, and what each means, stays with the consuming workspace (see `INTENT.md` § Non-Goals, "No operator policy").

#### Value Normalization

YAML-parsed values are normalized before storage:

| YAML Type | Stored As |
|-----------|-----------|
| `string` | unchanged |
| `int`, `float` | string (e.g., `"42"`, `"3.14"`) |
| `bool` | string (`"True"`, `"False"`) |
| `date` (bare `2026-03-08`) | ISO string (`"2026-03-08"`) |
| `datetime` | ISO string |
| `list` | preserved, items normalized recursively |
| `dict` | preserved, values normalized recursively |
| `null` | preserved as `None` |

Source: the retired index library, `__init__.py:282-300`

### Observation Syntax

An observation is a categorized fact about the entity. Written as a Markdown list item (lines starting with `- `).

**Canonical syntax:** `- [category] content text #tag1 #tag2 (context)`

| Part | Required | Pattern | Description |
|------|----------|---------|-------------|
| `[category]` | Yes | `[^\[\]()]+` inside `[]` | Classification label |
| content | Yes | free text | The fact or statement |
| `#tags` | No | `#\w[\w\-]*` | Inline tags, space-separated |
| `(context)` | No | trailing `(...)` | Source, date, or qualifier |

#### Parsing Rules

1. Only lines starting with `- ` (dash space) are candidates
2. Lines matching these patterns are **excluded**:
   - Task items: `- [ ]`, `- [x]`, `- [X]`, `- [-]` (regex: `^\[[ xX\-]\]`)
   - Markdown links: `- [text](url)` where the entire content is a single link
   - Bare wikilinks: `- [[Target]]` where the entire content is a single wikilink
3. Content is matched against `^\[([^\[\]()]+)\]\s+(.+)` for categorized observations
4. Lines with `[]` (empty brackets) default to category `"Note"`
5. Lines without brackets but containing `#tags` default to category `"Note"`
6. Lines without brackets and without tags are **skipped** (not observations)
7. Context is extracted from trailing parentheses: `rest.rfind("(")` to end, but only if the candidate does not contain `[[` (to avoid capturing wikilink targets)
8. Tags are extracted via `(?:^|\s)#(\w[\w\-]*)` and stripped from the content

Source: the retired index library, `__init__.py:150-156` (regex), `317-372` (parser)

#### Examples

```markdown
- [role] Senior engineer at Acme Corp
- [decision] Selected PostgreSQL over MySQL #database (based on benchmarks)
- [expertise] Machine learning
- [expertise] Natural language processing
- [progress] Completed Phase 1 design docs (2026-03-08)
```

Array-like fields use repeated categories (e.g., multiple `[expertise]` entries).

### Relation Syntax

Relations connect documents to form a knowledge graph. Two kinds:

#### Explicit Relations

List items containing `[[wikilink]]` targets.

**Syntax:** `- relation_type [[Target Entity]] (context)`

| Part | Required | Description |
|------|----------|-------------|
| `relation_type` | No | Text before `[[`. Defaults to `"relates_to"` if empty. |
| `[[Target]]` | Yes | Wikilink to target entity title |
| `(context)` | No | Parenthesized text after `]]` |

Parsing rules:

1. Only list items (`- `) containing both `[[` and `]]`
2. Task items are excluded
3. The first `[[...]]` in the line determines the target
4. Text before `[[` is the relation type (stripped). Empty = `"relates_to"`
5. Text after `]]` in `(...)` is context

#### Inline References

Wikilinks in non-list-item lines create implicit `links_to` relations:

```markdown
This approach builds on [[Core Design]] principles.
```

Creates: `links_to` relation to "Core Design".

Parsing: `\[\[([^\[\]]+)\]\]` regex applied to all non-list-item lines containing `[[`.

Source: the retired index library, `__init__.py:380-445`

#### Forward References

Relations can reference entities that don't exist yet. The `to_entity_id` is NULL until the target is created and indexed. Resolution happens at sync time by matching the target title to entity titles (case-insensitive, with alias support).

#### Common Relation Types

These are conventions, not enforced:

- **Organizational:** `works_at`, `works_on`, `reports_to`, `part_of`
- **Collaboration:** `collaborates_with`, `assigned_to`, `owned_by`
- **Dependency:** `depends_on`, `requires`, `contributes_to`, `supported_by`
- **Reference:** `relates_to`, `links_to`, `mentioned_in`, `learned_from`, `uses`
- **Events:** `organized_by`, `attendee`, `requested_by`

### Schema System

Entity types are validated against YAML schema files. Each schema defines:

```yaml
type: person                      # matches frontmatter type
frontmatter_promoted:             # fields recommended in frontmatter
  - email
  - company
  - role
  - tier
required_observations:            # must have at least one observation with this category
  - email
optional_observations:            # recognized categories (for validation warnings)
  - company
  - role
  - department
  - notes
optional_relations:               # recognized relation types
  - works_at
  - collaborates_with
  - reports_to
```

Source: the bundled knowledge skill's `schemas/person.yaml`

Schema files ship with the package in `m365_brain/schemas/`. Users can extend or override schemas via their config file (see `02-package-architecture.md`).

**Built-in schemas:** person, goal, task, project, note, learning, memory, rule, memo, calendar_event.

### Template System

Templates are markdown files with placeholder content for each entity type. They ship with the package and are copied into the user's workspace by `init`.

```markdown
---
title: {{title}}
type: person
tags: [person]
permalink: people/{{permalink}}
email: ""
company: ""
role: ""
tier: ""
---

# {{title}}

## Observations
- [email]
- [role]
- [company]

## Relations
- works_at [[]]
```

Templates live in `m365_brain/templates/` and can be overridden by user templates in `<workspace>/templates/`.

### Folder Conventions

The default workspace layout created by `init`:

```
<vault root>/
├── people/          # person entities
├── goals/           # goal entities
├── tasks/           # task entities
├── notes/           # general notes, learnings, memories
├── templates/       # user template overrides
└── _meta/
    └── knowledge.db # SQLite index (gitignored)
```

Every one of these names is a config value, not a constant — the vault root, each subdirectory, and the index path all come from the config file. Folder names are conventions for human organization. The entity `type` field in frontmatter — not the folder — determines the entity type for indexing and schema validation.

## Decisions

### D-05: Observation Syntax — Mandatory or Optional?

- **Context:** The `- [category] content` syntax is specific to this system. Users may have existing markdown files without it.
- **Options:**
  - A) Mandatory — only `[category]` lines are indexed as observations
  - B) Optional — also support plain list items as uncategorized observations
  - C) Configurable — user chooses parsing mode in config
- **Chosen:** A) Mandatory for v0.1
- **Consequences:** Plain list items without `[category]` are not indexed as observations. This is intentional: it avoids indexing every bullet point in a document as a "fact." Users who want everything indexed can use `[note]` as a catch-all category. We can add opt-in "index all bullets" mode in a future version if demand exists.

### D-06: Wikilinks — Required or Optional?

- **Context:** Wikilinks (`[[Target]]`) are the relation mechanism. Not all markdown ecosystems use them.
- **Options:**
  - A) Required — wikilinks are the only way to create relations
  - B) Optional — also support `[text](path)` standard links as relations
  - C) Configurable — user chooses link style
- **Chosen:** A) Required for v0.1
- **Consequences:** Wikilinks are simple, unambiguous, and well-supported by Obsidian, Logseq, and other PKM tools. Standard markdown links are URLs, not entity references — conflating them with relations would cause false positives. Users without wikilink support can still use the observation and search features without relations.

### D-07: Entity Type Extensibility

- **Context:** Built-in types (person, goal, task, etc.) cover common cases. Users will want custom types.
- **Options:**
  - A) Fixed set of types, no custom types
  - B) Any string is a valid type, schemas are optional validation
  - C) Custom types require a schema file
- **Chosen:** B) Any string is a valid type
- **Consequences:** The `type` field accepts any string. Built-in schemas provide validation for known types. Custom types without schemas are indexed normally but skip schema validation. Users can add custom schema files to get validation for their types.

### D-08: Folder Structure Enforcement

- **Context:** Should the package enforce a specific folder layout?
- **Options:**
  - A) Enforced — entities must live in type-matching folders
  - B) Conventional — `init` creates a default layout, but any `.md` file under the configured index roots is indexed
  - C) Configurable — mapping from type to folder in config
- **Chosen:** B) Conventional, not enforced
- **Consequences:** All `.md` files under the configured index roots are indexed regardless of folder. The entity type comes from frontmatter, not folder location. `init` creates a sensible default layout. Users are free to organize files however they want. This is also what lets the index cover trees the package did not produce, which `INTENT.md` names as a first-class capability.

## Open Questions

1. **Observation category normalization** — Should categories be case-normalized? Currently case-sensitive in the DB but case-insensitive when checking for frontmatter promotion conflicts.
2. **Multi-line observations** — The current parser is line-based. Should we support multi-line observations (indented continuation lines)?
3. **Schema enforcement level** — Should `validate` produce errors (fail) or warnings (inform) for schema violations? Likely warnings for v0.1.
4. **Template variables** — Simple `{{var}}` replacement or a real templating engine (Jinja2)?

## References

- Parser implementation: the retired index library, `__init__.py:150-156` (regex), `254-372` (frontmatter + observation parsers), `380-445` (relation parser)
- Note format reference: the bundled knowledge skill's `references/note-format.md`
- Schema files: the bundled knowledge skill's `schemas/*.yaml` (9 files)
- Entity templates: the consuming workspace's template directory
- Observation format origin: [basic-memory](https://github.com/basicmachines-co/basic-memory)
