---
title: "ADR 0011: The OneDrive AppFolder mirror is cut"
type: adr
permalink: adr-0011-cut-the-onedrive-appfolder-mirror
tags:
  - adr
---

# ADR 0011 — The OneDrive AppFolder mirror is cut

**Status:** Accepted (2026-08-05)

> This ADR records a decision **not** to build something. That is the kind a future implementer
> reverses by accident, so the operational history that justifies it is written down here rather
> than left in a commit log. Read the Consequences before reviving the feature.

## Context

The codebase being retired mirrored its vault into a OneDrive AppFolder, so the operator could see
the synced markdown inside their own OneDrive. Roughly 750 lines: a mirror worker (513) and a
OneDrive storage backend (237).

It is the highest-risk component in everything being absorbed. It is also the one that has already
caused a production incident.

What it buys, in this package's shape, is a second copy of a folder that is already a local folder
on the operator's machine. The vault is on disk. OneDrive's own client will sync a local folder if
the operator wants it in OneDrive, without this package participating.

## Decision

Cut it. The mirror worker and the OneDrive storage backend are not ported — not partially, not
behind a feature flag.

The feature is filed as a deferred task in this repository's backlog, carrying the rationale and
the scars below, so it is recoverable as a decision rather than erased.

## Consequences

**The incident this decision is made against.** A recursive AppFolder backlog produced a
**22-minute tick**: one cycle spent 22 minutes on mirror work, head-of-line blocking every other
job queued behind it. The loop was only visible once the queue was already full — a mirror that
can observe its own output is unbounded work, and the symptom appears downstream of the cause.

**The two hard-won fixes, which any revival must reproduce before the first mirror cycle runs:**

1. **Skip ALL `/Apps/<*>/` subtrees.** Not the one AppFolder this package owns — every AppFolder,
   belonging to any application. A skip narrowed to "our own" re-opens the loop the moment a
   second application registers its own AppFolder under `/Apps/`, and that failure looks like an
   unrelated slowdown.
2. **Block the AppFolder feedback loop at the source.** The loop is cut where content *enters* the
   mirror, not by filtering what comes out of it. Downstream filtering still enqueues the work, so
   it suppresses the visible symptom while preserving the unbounded queue that caused the
   incident.

A backlog entry without both fixes and the incident invites a re-implementation that reproduces
them, which is why they are stated here and in the task body rather than referenced.

**Further consequences:**

- The OneDrive storage backend goes with it, including its open upstream limitation: a 4 MiB
  single-shot PUT cap, with chunked upload unimplemented. Reviving the mirror means solving that
  too.
- Writing files into OneDrive or SharePoint is still supported — through the outbox's
  `file.update` intent (ADR 0001), with eTag `If-Match` optimistic concurrency and a loud failure
  on conflict. That is a deliberate, single-file, conflict-checked write. It is the opposite shape
  from a background mirror, and it is the shape that cannot produce a feedback loop, because
  nothing writes without an intent asking it to.
- If the mirror is ever revived, it must not be revived as "sync the vault to OneDrive". The
  requirement it actually served was operator visibility, and that has cheaper answers than a
  bidirectional mirror.
