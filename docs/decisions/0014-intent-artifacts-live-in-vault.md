---
title: "ADR 0014: The intent envelope and the dispatch vocabulary live in vault/"
type: adr
permalink: adr-0014-intent-artifacts-live-in-vault
tags:
  - adr
---

# ADR 0014 — The intent envelope and the dispatch vocabulary live in `vault/`

**Status:** Accepted (2026-08-05)

## Context

The design for this stage put the typed intent envelope, the payload union and the dispatch
vocabulary (`GraphOp`, `DispatchResult`, `DispatchReceipt`, the handler Protocol) under `outbox/`,
with the executors under `m365/outboxes/` importing them.

`scripts/check_structure.py` forbids that import. `outbox` and `m365` are **peers** at layer 4:

```python
"index": 4,
"outbox": 4,
"m365": 4,
```

and a same-layer import between two different subpackages is a finding in either direction. The
rule exists so the knowledge half of this package stays usable with Microsoft 365 absent entirely,
and the peer relationship between `outbox` and `m365` is stated deliberately in the map's own
comment.

So the design's dependency is not expressible: a handler must know the payload types, and the tier
guard must read what the handler declares, and no module may see both subpackages.

Three ways out were considered.

1. **Widen the layer map** to allow `m365 -> outbox`. Rejected: the check script is not this
   stage's to edit, and the peer relationship is load-bearing for the other half of the package.
2. **Invert through Protocols in `outbox/`** — a `GraphTransport` and a `FileWriter` the executors
   are typed against. Rejected: it puts Graph-shaped behaviour (Outlook HTML, `createReply`,
   upload sessions) inside the subpackage that is meant to be Graph-agnostic, and buys two
   interfaces with one implementation each.
3. **Push the shared types down** to a layer both peers may import.

## Decision

Option 3. `vault/` (layer 3) holds:

- `vault/payloads.py` — the five payload models and their discriminated union;
- `vault/intent.py` — `IntentEnvelope`, `parse_intent`, `dump_intent`;
- `vault/dispatch.py` — `GraphOp`, `DRAFT_ONLY_OPS`, `DispatchResult`, `DispatchReceipt`, and the
  `OutboxHandler` Protocol.

`outbox/` keeps the **lifecycle** (tiers, registry, stores, runner, reconciliation). `m365/`
keeps the **execution**. Neither imports the other, and `python3 scripts/check_structure.py` is
clean.

Two further consequences make the split work rather than merely satisfy the checker:

- **Handlers are injected.** `build_registry(config, profiles, handlers)` takes its handlers from
  the caller; `m365/outboxes/build_handlers()` constructs them. No module in the package imports
  both peers, and the wiring is one function the CLI calls.
- **`OutboxHandler` is a Protocol**, so an executor satisfies it structurally without importing it
  at all.

## Consequences

**This is arguably the better split, not merely the permitted one.** The intent file and the
receipt sidecar *are* vault artifacts: `VaultPaths` already names `outbox_intent`, `inflight`,
`processed` and the receipt, and `vault/classify.py` already judges outbox paths. The three-way
division that falls out — **vault owns the artifacts, outbox owns the lifecycle, m365 owns the
execution** — is sharper than the design's two-way one.

**`GraphOp` in `vault/` is the one awkward residue.** It is Graph vocabulary sitting in a
Graph-agnostic layer, and it is there because the guard that reads it and the handler that
declares it are on opposite sides of a wall. `vault/dispatch.py` says so in its docstring rather
than pretending otherwise.

**If the layer map is ever widened** to allow `m365 -> outbox`, `vault/payloads.py`,
`vault/intent.py` and `vault/dispatch.py` could move under `outbox/` with no behavioural change.
That is a rename, and this ADR is what tells a future reader it is safe.
