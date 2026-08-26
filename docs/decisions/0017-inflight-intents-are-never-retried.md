---
title: "ADR 0017: An in-flight intent is never auto-retried"
type: adr
permalink: adr-0017-inflight-intents-are-never-retried
tags:
  - adr
---

# ADR 0017 — An in-flight intent is never auto-retried

**Status:** Accepted (2026-08-05) — amended 2026-08-26, see Amendment below.

## Context

The dispatch sequence is: claim the intent, call Graph, write a receipt, archive. A crash between
the claim and the receipt leaves the intent in the in-flight directory with **no record of whether
the Graph call happened**.

Retrying is the obvious behaviour and it is wrong here. If the call succeeded and the process died
before the receipt, a retry sends a second copy of the same mail. Duplicating outgoing mail is
precisely the failure class an outbox exists to prevent; a stuck intent is not.

## Decision

An in-flight intent is reported and left alone.

- `pending()` never lists it — the source file is gone.
- `claim()` refuses when something is already in flight for that uuid.
- `push()` counts in-flight intents and logs them at warning level.
- Moving one back is a human act.

The archive is the replay ledger: `already_dispatched(uuid)` is true once **either** archive holds
the uuid, so a rejected intent is not retried either. Purging the processed archive re-arms replay.
That is a deliberate operator act and it is stated in `CONTRACTS.md` rather than guarded against.

## Consequences

- **The claim is not atomic, and the ceiling is named.** `StorageBackend` has no rename — it is
  read/write/delete over both a local directory and a blob container — so the claim is: refuse if
  something is in flight, read, write in flight, delete the source. A second runner starting after
  the in-flight write is refused; one starting inside the read-to-write window is not. Marked in
  `outbox/filesystem_store.py` with the upgrade path: one atomic `move` on `StorageBackend`.
- **No advisory lock.** The Postgres advisory-lock helper in the material this absorbed is not
  ported: it is Postgres-only, and its own implementation opened a fresh session per call, so its
  session-scoped lock survived by pooling accident.
- **An operator needs a way to see them.** `push()` returns the count and `store.inflight()`
  returns the uuids; a `status` verb surfaces them when the CLI lands.
- **The cost is a manual step after a crash.** Accepted deliberately: fail loud beats fail
  convenient when the thing being repeated is an email somebody already received.

## Amendment (2026-08-26) — a live transient failure is released, not stranded

"Moving one back is a human act" was written for the only case that existed: an intent found in
flight by a *later* process, with no record of whether Graph was called. That case is unchanged and
still needs a human.

`IntentStore.release(outbox_name, uuid)` adds a second, narrower case that is not that one. It runs
**in-process, in the same pass that claimed the intent**, on the `handler.execute()` failure path,
and only when the exception still carries a truthy `transient` — which a handler clears once it has
mutated Graph. So the uncertainty ADR 0017 refuses to gamble on is absent by construction: the
handler is the one thing that knows whether a request landed, and it is answering.

Without it the alternative was not "wait for a human". `claim()` has already deleted the source
file and `already_dispatched()` reads the archive, so an identity-provider blip archived the intent
as permanently `failed` and the draft could never be re-attempted — a silent drop, which is the
failure ADR 0017 exists to prevent, arriving by the other door.
