---
title: "ADR 0022: Bundled skills are package-prefixed and have no environment"
type: adr
permalink: adr-0022-skills-are-prefixed-and-environmentless
tags:
  - adr
---

# ADR 0022 — Bundled skills are package-prefixed and have no environment

**Status:** Accepted (2026-08-05)

## Context

The three skills this package bundles descend from a private workspace's own tooling, where they
were called `knowledge`, `files` and `ops`, each carried its own package environment and lockfile,
and each documented a recovery procedure for the race that environment produced when several agents
invoked it at once.

Two problems came with that shape into a public package. Installed skills land in **one flat tree**
alongside everyone else's, so a skill literally called `files` is a namespace grab that collides on
the first install. And a skill with its own environment has to be installed, kept in step with the
library it wraps, and repaired when the two disagree.

## Decision

**Names are prefixed:** `m365-brain-knowledge`, `m365-brain-files`, `m365-brain-ops`. All three are
under the spec's 64-character limit, contain only `a-z0-9` and single hyphens, and equal their
parent directory name — which the spec requires and `scripts/check_structure.py` now enforces.

**The skills have no environment of their own.** No package manifest, no lockfile, no editable
install. They shell out to the already-installed `m365-brain` console script, and read their config
path from a single environment variable, `M365_BRAIN_CONFIG`.

That variable is the whole configuration seam. There is no `__file__`-relative path arithmetic and
no walking up a directory tree looking for a workspace root — which is precisely the assumption
that made the originals non-portable.

## Consequences

- **Three lockfiles and an entire class of gotcha are deleted.** The race, its symptom and its
  recovery procedure all stop existing, because there is nothing to install.
- **The skills document the CLI, and the operator documents their own conventions.** No folder
  tree, no note-type vocabulary, no "write only to X" rule appears in a `SKILL.md`. Those were the
  single largest reservoir of private vocabulary in the source skills, and a CI grep now rejects
  them.
- **A verb has to exist before a skill can mention it.** This is a constraint and a good one: it
  forced the library to grow the surface rather than the skill to grow a script.
- **The version-string trap is checked, not trusted.** `metadata` values are strings by
  specification, so an unquoted `version: 1.0` is a float and invalid; `allowed-tools` is a
  space-separated string, not a list. Both are asserted by `check_structure.py` and confirmed by
  the specification's own validator.
- **`m365-brain-files` keeps exactly one script.** `ocr_extract.py` shells out to `ocrmypdf` and
  then to the CLI, because OCR is a preprocessing step the library does not perform. Everything
  else the source skills carried either became a verb or was ruled out.
