---
title: "ADR 0009: Rename the package to m365-brain"
type: adr
permalink: adr-0009-rename-to-m365-brain
tags:
  - adr
---

# ADR 0009 — Rename the package to m365-brain

**Status:** Accepted (2026-08-05)

## Context

The package was called `m365-extract`, and the name was accurate: it extracted from Microsoft 365
and wrote markdown.

After the consolidation it also owns a knowledge layer (ADR 0006), a typed write-back outbox
(ADR 0001), a scheduler with a change manifest and hooks (ADR 0007), and the agent skills that
drive all of it (ADR 0008). "Extract" now names one of four capabilities.

A name that undersells a package is not a cosmetic problem. It is an invitation to build the
hidden parts again somewhere else, because nobody looks for an outbox or a search index inside
something called "extract".

## Decision

Rename across every surface in the working tree: repository, distribution (`m365-brain`), import
package (`m365_brain`), console script (`m365-brain`, with `mb` as a short alias), pixi workspace,
documentation, and URLs. The directory move uses `git mv` so history follows.

`CHANGELOG.md` is **not** rewritten — it is release history, and rewriting it would make the
release record disagree with the releases.

The name keeps `m365` first, and that ordering is the point: it commits the package to being
Microsoft-365-specific. See ADR 0012, which records the rejection of a multi-source abstraction
and is the substantive half of this naming choice.

## Consequences

- A one-word name was rejected as too collision-prone for a published distribution; a prefixed
  name is both available and honest about the scope.
- Renaming the GitHub repository, the PyPI project, and the documentation URL are actions outside
  the working tree and need a human. GitHub redirects the old repository path, so an existing
  submodule URL keeps resolving until it is deliberately updated.
- Existing installations of the old distribution are not migrated and no compatibility alias is
  published. The package is pre-1.0 in intent — `production_gate: false` — and an alias would be
  a permanent obligation acquired to save one `pip install`.
- The rename lands as one commit containing nothing else, before any file moves, so that the
  mechanical diff stays reviewable and the test count is the correctness signal.
