# Postgres adapters for IndexBackend, IntentStore, StateStore

Deferred by ADR 0010. The protocols ship with the single-user implementation; this task is the
second implementation that proves they are seams rather than decoration.

## Goal

Set `index.backend: postgres` in config and everything works, with no code change in any caller.

## Requirements

- `IndexBackend` over Postgres: `tsvector` where SQLite uses FTS5, `pgvector` where SQLite uses
  sqlite-vec.
- `IntentStore` over Postgres rows, replacing the filesystem-and-JSON implementation.
- `StateStore` over Postgres, replacing JSON under the vault's `_meta/` directory.
- An `EmbeddingProvider` calling a hosted embedding endpoint rather than running fastembed
  in-process — a shared service should not pay a per-process model load.

## Acceptance Criteria

- [ ] **Every existing test passes against the Postgres implementation unmodified.** The suite is
      already parametrized over each protocol's in-memory fake, so this is a third parameter, not
      a parallel suite.
- [ ] No caller outside `index/backends/` and `outbox/stores.py` changes.

If a test needs editing to pass, the protocol was leaking its SQLite implementation and the
protocol is what should change. If callers need touching, the seam was decorative — that is worth
its own ADR, and it makes the design note claiming otherwise wrong.

## Non-goals

- Migrating existing SQLite data. Both indexes derive from the vault and rebuild from it.
- Choosing a hosting story — that is `08-05-container-apps-deployment`.
