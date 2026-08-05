---
title: "ADR 0012: No multi-source abstraction — Microsoft 365 only"
type: adr
permalink: adr-0012-no-multi-source-abstraction
tags:
  - adr
---

# ADR 0012 — No multi-source abstraction — Microsoft 365 only

**Status:** Accepted (2026-08-05)

> Like ADR 0011, this records a decision **not** to build something, and it is the one most likely
> to be reversed casually — by adding a `SourceBackend` "while we're in here". It is a separate
> ADR rather than a paragraph inside the rename (ADR 0009) because it is a scope decision that
> outlives the naming choice: the name follows from this, not the other way round.

## Context

The codebase being retired had the abstraction already: a `SourceBackend` protocol, an
integrations registry, and `Integration` plus `IntegrationGroupAssignment` tables — a plugin seam
for data sources that never acquired a second source.

The pull to keep it on absorption is strong and entirely speculative. The costs are not:

- Every extractor change is paid twice, once in the concrete code and once in the shared shape.
- An abstraction designed against one example is a description of that example. The second SaaS
  source will differ in authentication model, delta semantics, rate-limit behaviour, and what a
  "message" even is — and the guessed protocol will fit none of it.
- A plugin slot advertises an extension point the package does not actually support, which invites
  contributors to build against a seam nobody maintains.

## Decision

`m365_brain/m365/` is a **namespace, not a protocol**. There is no `SourceBackend`, no source
registry, no integrations table, and no plugin indirection. The retired codebase's `sources/`,
registry, and integration tables are explicitly not ported.

The package is Microsoft 365 only, and its name says so (ADR 0009).

A second SaaS source is a future decision with a future ADR, taken when there are two real
examples to design against.

## Consequences

- The boundary that actually matters is kept regardless: **nothing under `m365/` is imported by
  the knowledge layer**, and `index → m365` is a hardcoded same-layer rejection in
  `scripts/check_structure.py`. The knowledge half indexes ordinary markdown with no Microsoft 365
  present. That edge — not a protocol — is what would make a later split cheap.
- Adding a second source later is a refactor against two known shapes. That is strictly better
  information than the guess this ADR declines to make, and the refactor is cheap because the
  layering already separates the halves.
- Extractors stay direct and readable. The eight of them are the most battle-tested code in the
  package, and they move by path rather than by rewrite; a registry indirection would have made
  that move a rewrite.
- Reversing this ADR is a design decision requiring its own ADR, not a refactor. If a
  `SourceBackend` appears without one, this decision has been reversed by accident.
