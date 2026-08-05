---
title: "ADR 0021: The index is a schedule unit, not a step after extraction"
type: adr
permalink: adr-0021-index-is-a-schedule-unit
tags:
  - adr
---

# ADR 0021 — The index is a schedule unit, not a step after extraction

**Status:** Accepted (2026-08-05)

## Context

The implementation this replaces indexed in two places: a full-text sync fired at the end of every
extraction pass, and a separate reindex job on its own timer. Two mechanisms, one job, and neither
knew about the other.

It also meant a configured index root that no extractor writes into — a folder of hand-written
notes, an export somebody dropped in, anything the operator owns — was only indexed by the second
mechanism, as a special case. "Indexing roots the library did not produce is first-class" was true
only in the sense that a separate cron job existed.

## Decision

The index is a unit in the same schedule as the eight extractors, with its own interval
(`index.sync.interval_minutes`) and its own cursor in the same namespace.

The cycle runs the index step when **either** something changed this cycle **or** the index unit is
due:

```
index step runs  ⟺  (any extractor recorded a change)  ∨  (the index unit is due)
```

The first disjunct keeps the vault searchable immediately after an extraction. The second is what
makes a root nobody extracts into literally first-class rather than a documented exception.

Vector embedding happens inside that step, synchronously. There is no background thread.

## Consequences

- **One mechanism, one place to look.** `status` shows the index beside the extractors, with the
  same last-run / last-success / failure-streak shape.
- **Cadence changes.** A root that used to be indexed only after an extraction now indexes on its
  own interval. That is the intent, and it is worth watching the first day's cycle timings —
  `interval_minutes` is the knob.
- **A cycle that reports done is done.** The background vector-sync thread it replaces meant a
  cycle could report success while a thread was still writing the vector store, and needed a
  module-level "am I already running" flag — shared mutable state — to avoid overlapping with
  itself. Both are gone. If embedding is slow, `index.vector.embed_batch_size` and
  `index.vector.threads` are the knobs.
- **An index failure does not stop a cycle.** It is recorded as `IndexOutcome.errors`, the hooks
  still fire, and `manifest.ok` is false.
- **The index unit only exists when there is an `index:` section.** A Microsoft-365-only deployment
  never sees it, which is the same section-optionality every other subsystem has.
