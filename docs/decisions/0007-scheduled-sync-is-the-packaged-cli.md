---
title: "ADR 0007: Scheduled sync is the packaged CLI; consumer behaviour attaches as hooks"
type: adr
permalink: adr-0007-scheduled-sync-is-the-packaged-cli
tags:
  - adr
---

# ADR 0007 — Scheduled sync is the packaged CLI; consumer behaviour attaches as hooks

**Status:** Accepted (2026-08-05)

## Context

The sync application being absorbed was a daemon wrapping this package. Beyond scheduling, it did
four things the package did not: it indexed the vault after each cycle, it appended to a queue
file that a downstream agent watched, it triggered a second downstream workflow, and — to know
what had changed since the previous cycle — it re-scanned the filesystem and kept seen-set
watermark files to deduplicate against.

Three of those four are one consumer's behaviour. The fourth, indexing, is the package's own job
and was outside it only because the package had no index. If the wrapper survives, every adopter
inherits either that consumer's opinions or the obligation to write their own daemon.

Re-scanning deserves separate attention: the cycle already knows exactly what it wrote. Scanning
the filesystem afterwards to rediscover it, then keeping a watermark file so the next scan does not
re-report it, is work to recover information that was thrown away one function call earlier.

## Decision

There is no wrapper application.

- A downstream consumer runs the packaged CLI — `run` — under whatever supervisor it prefers.
- Indexing is a configured step of the cycle: extract → manifest → index → hooks.
- Everything consumer-specific attaches as a **post-cycle hook**: a dotted-path callable declared
  in config, resolved with `importlib`, called with the typed change manifest. A hook that raises
  is logged and does not abort the cycle.

## Consequences

- The library never learns its consumers exist. Adding a downstream behaviour is a config line and
  a function in the consumer's own codebase.
- Hooks receive the manifest instead of re-scanning, which **deletes the watermark files
  outright** — the manifest is the watermark. That is a simplification, not a relocation.
- Hooks are in-process callables, not subprocesses, so no PATH, shell, or environment assumption
  crosses the boundary. The consumer supplies the callable; the consumer owns its failures.
- The logged-and-continue rule for a raising hook is the one deliberate exception to fail-loud in
  this package. It is scoped to consumer code precisely so one consumer's bug cannot wedge
  extraction, and it is the reason a hook is not an acceptable place to put anything the cycle's
  correctness depends on.
- Delivered by the runtime stage (R2, R3, R5).
