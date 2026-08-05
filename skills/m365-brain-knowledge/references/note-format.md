# What the index reads out of a markdown file

The *structure* below is fixed. Every *name* in it is a config value, so read
`m365-brain --config "$M365_BRAIN_CONFIG" config show --json` before assuming
any particular key or category exists.

## The shape

```markdown
---
title: Project Atlas
type: project
permalink: project-atlas
tags: [active, platform]
aliases: [Atlas]
status: green
---

Prose. Indexed as content.

## Observations

- [status] Green as of the last review #health
- [decision] Chose the managed option (cost) 

## Relations

- depends_on [[Project Beacon]]
- [[Team Platform]]
```

## Frontmatter

Five keys are lifted into typed columns, and which key holds each is config:

| Column | Config key | Purpose |
|---|---|---|
| title | `index.frontmatter.title_key` | display name |
| type | `index.frontmatter.type_key` | `--type` filter; falls back to `default_type` |
| permalink | `index.frontmatter.permalink_key` | the stable identifier |
| tags | `index.frontmatter.tags_key` | `--tag` filter |
| aliases | `index.frontmatter.aliases_key` | alternate names `index context` resolves |

**Every other frontmatter key becomes a searchable observation**, unless it is
listed in `index.frontmatter.structural_keys`. That list is the contract
between whatever writes the files and what the index makes searchable: a key
that is structure rather than content belongs in it, and a key that is content
must stay out of it to remain findable.

A permalink already claimed by a different file is replaced with one derived
from the file's own path, and the substitution is logged. Two files cannot
share a permalink.

## Observations

`- [category] text #tag (context)` — the category and the parenthesised context
are optional. A line with no category takes
`index.observations.default_category`.

Observations are what `--field` filters against and what `index context`
returns beside an entity.

## Relations

| Written as | Relation type |
|---|---|
| `- verb [[Target]]` | `verb` |
| `- [[Target]]` | `index.relations.explicit_default_type` |
| a `[[wikilink]]` in prose | `index.relations.inline_type` |

A link to something not yet indexed is stored unresolved and resolves itself
on a later sync — so a forward reference is normal, and a relation whose target
is `null` means the target has not been indexed *yet*, not that it is missing.

## What is not indexed

- Files whose extension is not in `index.file_extensions`.
- Anything matching `index.exclude`.
- Anything outside `index.roots`. Roots are declared, never discovered.
