---
title: "ADR 0005: Big-bang cutover, tracked as six children"
type: adr
permalink: adr-0005-big-bang-cutover
tags:
  - adr
---

# ADR 0005 — Big-bang cutover, tracked as six children

**Status:** Accepted (2026-08-05)

## Context

This consolidation collapses five units into one: this package, a knowledge-index library, a sync
application, a separate outbox-and-deployment codebase, and a package of design documents.

A staged migration would mean running old and new paths at the same time: two writers into the
same vault, two indexes over the same markdown, two sets of state files, and a compatibility shim
at every boundary where the shapes differ. Every shim is code written to be deleted, and the
period during which both paths are live is exactly the period in which a divergence is hardest to
notice.

The usual reason to accept that cost is irreversible data movement. It does not apply here: the
content being restructured is Graph-derived and regenerable, so the worst case of getting the
cutover wrong is re-pulling it. What is *not* regenerable — hand- and agent-authored markdown — is
out of the restructure entirely and is only ever read.

## Decision

One cutover. No dual-write period, no compatibility shims, no staged migration.

The deliverables are tracked as a parent with six children — self-containment, knowledge layer,
M365 platform, runtime and CLI, consumer simplification, and retirement of the superseded
repository — because each verifies independently. The *tracking* is staged; the *switch* is not.

## Consequences

- A re-ingest window during which Microsoft 365 content is missing from search, bounded by the
  slowest extractor. SharePoint is the bound: 25 000 items, delta-paged, and the initial delta
  does not complete within the configured page cap on the first pass.
- No shim code to write and no shim code to delete afterwards.
- Rollback is "re-ingest", not "revert a migration". That is only true because the ingested tree
  is regenerable, which is why authored content is excluded from the restructure.
- Each child stage carries its own acceptance criteria and can be verified before the next
  begins, so the single switch is not a single untested leap.
