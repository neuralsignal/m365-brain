---
title: "ADR 0006: The knowledge layer folds into this package"
type: adr
permalink: adr-0006-knowledge-layer-folds-into-this-package
tags:
  - adr
---

# ADR 0006 — The knowledge layer folds into this package

**Status:** Accepted (2026-08-05)

## Context

The consuming workspace kept its knowledge index as a separate library: 1231 lines in a single
module holding the entity model, the markdown parsers, the FTS index, the vector index, the
incremental sync, the graph traversal, and the file catalog, with 22 direct importers. Alongside
it sat a package of nine design documents that already specified how that monolith should
decompose — a module layout, a config model, and connector thinking that had never been built.

Two options for the fold. Keep the index a separate distribution and depend on it: that is two
packages, two release cycles, two config roots, and a version-compatibility edge — for something
with exactly one consumer shape, namely "index the vault this package writes, plus whatever else
the operator writes". Or fold it in, and accept that one unit then owns both a knowledge model and
a Microsoft 365 integration.

The second is honest about the coupling that already exists: the index's only interesting input is
the vault, and the vault's only interesting consumer is the index.

## Decision

The knowledge layer becomes part of this package — `m365_brain/model.py`, `parsers/`, `index/`,
and the `workspace.py` facade — decomposed per the design documents rather than relocated as a
monolith. The nine design documents move into `docs/design/` as its blueprint. The separate
library is retired and its importers move to this package's API.

The unit therefore owns two things. That is the accepted consequence, and the rename (ADR 0009)
exists to make it visible in the name rather than hidden behind one.

## Consequences

- The boundary between the halves becomes *structural* rather than distributional: `index/` never
  imports `m365/`, enforced by `scripts/check_structure.py` as a hardcoded same-layer rejection.
  The knowledge layer must index a scratch directory of ordinary markdown with no Microsoft 365
  present — if that path needs an `m365` import, the decomposition is wrong.
- A future split back into two distributions stays cheap precisely because of that edge. This ADR
  trades distribution independence for one repository, not for entanglement.
- Every module constant in the retired library becomes config: vector dimensions, embedding model
  name, chunk size, chunk overlap, and its folder conventions. That list is not incidental — it is
  the specific reason the old library was unusable by anyone else.
- The existing property-based tests are ported rather than re-authored. They encode parser edge
  cases nobody remembers, and re-writing them would quietly re-decide them.
- Equivalence is proven before the old library is deleted, not after: indexing the same tree must
  produce the same entity, observation, and relation counts.
- Delivered by the knowledge-layer stage (K1–K6).
