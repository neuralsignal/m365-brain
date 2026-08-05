---
title: "ADR 0010: The deployed multi-user service is deferred; the seams are not"
type: adr
permalink: adr-0010-deployed-service-deferred-seams-kept
tags:
  - adr
---

# ADR 0010 — The deployed multi-user service is deferred; the seams are not

**Status:** Accepted — deferred (2026-08-05). Deferred by the absence of a second operator and of
anywhere to deploy to. Revisit when someone other than the first operator needs isolated state.

## Context

A multi-user deployed service is genuinely half-built here. There is a Reflex admin UI, Alembic
migrations, a Postgres driver, an OAuth2 authorization-code flow, per-user token encryption at
rest, per-user storage isolation, a worker running independent per-`(user, extractor)` jobs with
advisory locks, and a dev deployment that ran end to end.

What is missing is not the UI. It is RBAC, audit logging, rate limiting, JWT validation, group
assignment, and Postgres-backed persistence behind the storage, index, state, and intent
boundaries — plus the hosting and provisioning of ADRs 0002 and 0003.

Two ways to get it wrong. Build it now, against zero users, and carry the maintenance of a
security surface nobody exercises. Or defer it and write single-user code with the boundaries
baked flat, so that the eventual service is a rewrite rather than an adapter — which is how the
deferral becomes permanent.

## Decision

Defer the service. Land the seams now.

Five protocols ship in this consolidation, each with exactly one implementation **and an
in-memory fake**:

| Protocol | Now | Deferred adapter |
|---|---|---|
| `IndexBackend` | SQLite + FTS5 | Postgres + tsvector |
| `EmbeddingProvider` / `VectorStore` | fastembed + sqlite-vec | pgvector, hosted embeddings |
| `IntentStore` | filesystem + JSON | Postgres rows |
| `StateStore` | JSON under the configured meta directory | Postgres |
| `StorageBackend` | local filesystem, Azure Blob | already sufficient |

Backend selection is a config value, not a code path. Auth already supports N named profiles
(M6), which is the multi-tenant shape at the credential layer.

The deferred work — Postgres adapters, groups, audit log, rate limiting, JWT validation, the
Container Apps Bicep (ADR 0002), and the Entra provisioning script (ADR 0003) — is authored as
tasks in *this* repository's backlog, where the code is, rather than in a consumer's.

## Consequences

- The service becomes an adapter plus a config value. That claim is the entire justification for
  deferring, so it has to be true rather than asserted.
- A protocol with one implementation is unfalsifiable, so each ships an in-memory fake and **every
  test against the real implementation must pass against the fake unmodified**. The fake is cheap
  and it is the only thing keeping a one-implementation seam honest; without it the protocols rot
  into decoration within two refactors.
- `m365_admin/` is **kept**, not deleted. It is working, tested, CI-covered code that conflicts
  with nothing in the plan, and it is the deployed-shape seam this ADR says to preserve. Deleting
  working code as a side effect of a refactor is out of scope for a stage whose stated non-goal is
  behaviour change. It also means the admin-UI half of the deferred work is already done.
- The repository therefore has two top-level Python packages. `scripts/check_structure.py` applies
  the import-direction and module-size rules to `m365_brain/` only — a Reflex application has its
  own conventions, and imposing a library's layering on a UI would be rule-making for its own sake.
- Until the adapters exist the package is single-process and single-operator. Concurrency
  protection beyond SQLite's busy-timeout is not implemented, and that is a consequence of this
  deferral rather than an oversight.
