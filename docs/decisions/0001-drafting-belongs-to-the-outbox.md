---
title: "ADR 0001: Drafting belongs to the outbox"
type: adr
permalink: adr-0001-drafting-belongs-to-the-outbox
tags:
  - adr
---

# ADR 0001 — Drafting belongs to the outbox

**Status:** Accepted (2026-08-05)

## Context

Two of the codebases being consolidated could write back into Microsoft 365, and they had solved
different halves of the problem.

The sync application had a *working* write path, grown in production against a real mailbox: draft
push, a sender, and reconciliation of what happened to a draft after a human touched it —
including attachment handling, inline images, and signature insertion. What it did not have was a
schema. The intent format was whatever the caller happened to write, and the permission model was
"this code only ever creates drafts".

The other codebase had the opposite: a typed intent envelope, an idempotency key, a permission
tier per outbox, lifecycle states, and archiving — and no sender at all, no attachments, no inline
images, no signatures.

Keeping write-back split across a "sync" unit and an "outbox" unit means two places that must
agree on a wire format neither one owns, and two places to change when a payload field is added.
The failure mode is not dramatic; it is a payload that validates in one unit and is silently
dropped by the other.

## Decision

Write-back is one concern and it lives here, in the outbox.

The sync application's draft-push, sender, and reconciliation code moves into this package:

- `m365_brain/outbox/` — the transport-agnostic half: intent envelope, permission tiers,
  `IntentStore` protocol, the runner, and reconciliation classification.
- `m365_brain/m365/outboxes/` — the Graph-specific half: outbox definitions and per-outbox
  Pydantic payload schemas (`extra="forbid"`).

The typed envelope wins as the format; the sender's behaviour wins as the implementation. The
attachment, inline-image, and signature handling folds in with it. Reconciliation is a pass of the
outbox runner, not a worker of its own.

Outboxes: `email.draft`, `email.reply`, `email.forward` (all `draft_only`), `teams.post_message`,
`file.update`.

## Consequences

- One wire format, defined once, validated once. A payload that the schema rejects never reaches
  Graph.
- The outbox is the only path to a Graph write. There is no second, untyped write path left
  behind for convenience.
- Losing the attachment, inline-image, or signature handling during the move would be a silent
  regression in behaviour that works in production today, so the port is asserted against a
  recorded request body rather than eyeballed.
- The locale-aware amendment classifier comes across wholesale. Replaying the existing drafts
  corpus must reproduce today's verdicts; a disagreement is a port bug, not an improvement.
- Delivered by the M365-platform stage (M3, M4). Until then `CONTRACTS.md` marks the outbox
  surface pending.
