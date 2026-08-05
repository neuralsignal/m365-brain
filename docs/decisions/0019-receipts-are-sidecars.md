---
title: "ADR 0019: The receipt is a sidecar and the archived intent is byte-identical"
type: adr
permalink: adr-0019-receipts-are-sidecars
tags:
  - adr
---

# ADR 0019 — The receipt is a sidecar and the archived intent is byte-identical

**Status:** Accepted (2026-08-05)

## Context

Something has to record what happened to an intent. The implementation this ports wrote the reason
**into the archived file's frontmatter** (`rejection_reason:`), which meant every archived file
then failed its own `extra="forbid"` when re-read. They worked around it by never re-reading the
rejected archive — a workaround that quietly forbids the archive from being useful for anything
else.

## Decision

The archived intent is written **byte-identical** to what was submitted. The outcome goes in a
sidecar beside it:

```
<meta>/<processed>/<uuid>.md            the intent, unchanged
<meta>/<processed>/<uuid>.receipt.json  DispatchReceipt
<meta>/<processed>/<uuid>.reconciled.json  terminal verdict, written later
```

with the same pairing under `<rejected>` for a blocked or failed intent — a rejection has to say
why as much as a dispatch does.

`DispatchReceipt` carries `uuid`, `kind`, `outcome` (`dispatched` / `rejected` / `blocked`),
`dispatched_at`, `graph_message_id`, and a `reason` drawn from a **closed set**:

```
tier_blocked · no_approval_recorded · etag_conflict · graph_error
attachment_missing · parse_error · unknown_outbox
```

plus a free-text `detail`. The reason is closed because an operator greps receipts by it; free text
would make the archive unqueryable exactly when someone needs to ask why forty intents failed.

## Consequences

- **The archived intent is the snapshot reconciliation diffs against.** The implementation this
  replaces wrote a parallel `drafts/snapshots/` tree and carried its path in frontmatter to answer
  the same question. In an immutable archive the intent already *is* the snapshot, so
  `snapshots/`, `body_snapshot_path` and the body-changed comparison all disappear — three moving
  parts replaced by one that had to exist anyway.
- **The archived files are also the parity fixtures.** They parse under their own schema, so a
  recorded dispatch can be replayed.
- **`archive()` is one call.** It writes the intent, writes the receipt and clears the in-flight
  entry together, so a caller cannot half-archive.
- **The terminal reconciliation verdict is a third, separate file.** A receipt is immutable, so the
  verdict cannot be an edit to it. Without the marker the reconciliation pass would re-fetch every
  draft it has ever sent, on every run, forever.
