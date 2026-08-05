---
title: "ADR 0015: The amendment classifier has never run on production data"
type: adr
permalink: adr-0015-amendment-path-unobserved
tags:
  - adr
---

# ADR 0015 — The amendment classifier has never run on production data

**Status:** Accepted (2026-08-05)

> This ADR records a **limit on what a green test proves**. It exists because the gate that covers
> this code passes, and a passing gate is exactly the thing that stops people asking.

## Context

`outbox/reconcile.py` decides whether a human edited an AI-written draft before sending it. It is
the hardest-won logic in the material this package absorbed: a quote-marker table, a
markdown-to-text flattening, a truncation at the earliest marker, and a four-branch containment
heuristic.

The consuming workspace it came from holds, at the time of the port:

| | count |
|---|---|
| pending drafts | 13 |
| rejected drafts | 48 |
| body snapshots | 61 |
| **sent records** | **0 — the directory does not exist** |

So the `rejected` (Graph 404) and `pending` (`isDraft: true`) branches have real production
history behind them. The `sent` and `amended` branches — the ones the heuristic exists for — have
**none**. The classifier has never been observed making the judgement it was written to make.

## Decision

Port the heuristic unchanged, and verify it by **differential replay** rather than by assertion.

`scripts/replay_reconcile.py` (throwaway, not committed) loads the previous implementation's
classifier verbatim out of its own source file and runs both over the same inputs:

- the 61 real snapshot bodies, each paired with a synthesised sent body — the draft as rendered by
  the sender, an edited rewrite, a prefixed variant, an emptied one, and one per configured quote
  marker: **610 cases**;
- the 48 real rejected drafts through the 404 branch;
- the 13 real pending drafts through the `isDraft` branch.

Result at the time of writing: **0 mismatches**, verdict distribution `sent: 488, amended: 122,
rejected: 48, pending: 13`.

## Consequences

**What the gate proves.** That this package reproduces the previous implementation's *code* on
inputs derived from real bodies. Every branch of the heuristic is exercised, and the two
implementations agree on all of them.

**What it does not prove.** That either implementation classifies real human edits correctly.
Nobody has ever seen it do so. "Reproduces today's verdicts" means "reproduces today's code", not
"reproduces today's data", and no amount of green here changes that.

**What follows.** The first weeks of real `sent` records are the actual test. Until then, treat
`amended` as a hint for a human reviewer rather than a measurement — which is what the heuristic's
own docstring says, and is why the containment rule is deliberately coarse.

**The quote-marker table is config** (`outboxes.reconcile.quote_markers`) precisely because of
this. Its entries are locale-specific and one of them is a personal sign-off phrase; compiling a
never-validated heuristic's inputs into the package would ship one user's habits as a library
constant.
