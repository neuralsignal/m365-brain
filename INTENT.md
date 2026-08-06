---
title: "Intent: m365-brain"
type: intent
permalink: intent-m365-brain
version: "0.1"
status: development        # development | production
production_gate: false     # true ONLY when deployed AND in active use
tags:
  - intent
---

# Intent: m365-brain

> The agnostic source of truth for this unit. Code serves this document, not the reverse.
> Language/SDK details belong in `docs/`, not here.

## Goal

Give a person or a small team a durable, local, greppable copy of their Microsoft 365 working
context — mail, calendar, chats, channels, files, contacts, directory — as markdown; make that
copy searchable alongside whatever else they already write in markdown; and let an agent or a
script act back into Microsoft 365 through a reviewable, permission-gated outbox.

Three capabilities, one package, one config file:

1. **Sync** — Microsoft 365 into a configurable local vault of markdown, via the Graph API,
   incrementally.
2. **Index** — any configured markdown tree, *including trees this package did not produce*, for
   full-text, vector, and hybrid search plus entity/relation traversal.
3. **Write back** — typed intents that an outbox *authority* gates before they reach Graph, with
   reconciliation of what actually happened to them.

The unit exists to be adoptable by a stranger. Every folder name, tree shape, file-naming rule,
interval, threshold, and model choice is a config value. A different layout is a config edit,
never a code change. That single property is what separates this from one person's sync script
with a `pyproject.toml` attached.

## Scope

**Extraction.** Eight Graph extractors (email, calendar, Teams chats, Teams channels, OneDrive,
SharePoint, contacts, directory) rendering markdown with YAML frontmatter, with delta tokens,
pagination, retry, and throttling handling. Document conversion for binary attachments. Persisted
through a `StorageBackend` (local filesystem or Azure Blob).

**Vault contract.** `inbox/` (read-only upstream truth) · `annotations/` (agent-authored
overlays) · `outbox/` (typed intents) · `_meta/` (state, cursors, manifests), behind one path
builder, with **every directory name — including per-extractor output names — coming from
config**.

**Deletion lifecycle.** One canonical upstream-removal handler that all extractors route through,
plus disable-cleanup that purges vault subtree, sync state, and status.

**Knowledge layer.** `Entity` / `Observation` / `Relation`, markdown parsers, an `IndexBackend`
with a SQLite/FTS5 implementation, pluggable embedding provider and vector store with RRF hybrid
fusion, checksum-driven incremental sync over an explicitly configured list of roots, graph
traversal, and the discovered-file catalog with its conversion lifecycle.

**Write-back.** Typed intent envelope with a client-supplied idempotency key, per-outbox payload
schema, a four-level authority router (`never_auto` / `human_approval` / `draft_only` /
`auto_send`), lifecycle states, `_processed/` and `_failed/` archiving with a machine-readable
reason, and locale-aware reconciliation that classifies an executed intent as sent / amended /
rejected / pending.

**Graph plumbing.** One transport (retry, backoff, throttling, 401-refresh); named auth profiles
so N Entra apps with different client ids, scopes, and token caches coexist; site/drive
resolution and `get_file` / `put_file` with eTag `If-Match` optimistic concurrency.

**Runtime.** A scheduler with per-extractor intervals, a typed change manifest per cycle,
post-cycle hooks as config-declared dotted-path callables, and a CLI that operates all of the
above from a config file with no code written by the operator.

**Bundled agent skills** (`skills/{knowledge,files,ops}`) as thin wrappers over the CLI and the
`workspace.py` facade.

**A multi-user admin surface** (`m365_admin/`): Reflex UI, Alembic migrations, per-user token
encryption and storage isolation. It exists, it is CI-covered, and it is the deployed-shape seam
the deferred service (ADR 0010) will grow into.

## Non-Goals

- **No multi-source abstraction.** This package is Microsoft 365 only. `m365/` is a namespace,
  not a `SourceBackend` protocol — there is no source registry and no plugin slot waiting to be
  filled. A second SaaS source is a future decision with a future ADR, designed against two real
  examples rather than one imagined one. (ADR 0012.)
- **The deployed multi-user service is deferred.** Postgres-backed adapters, groups, audit log,
  rate limiting, JWT validation, Container Apps deployment, and Entra provisioning are backlog
  items in this repo, not code in this release. The protocol seams land now so that service is
  later an adapter plus a config value; the adapters themselves are not built. (ADR 0010, with
  ADRs 0002/0003/0004 recording the hosting, provisioning, and deploy-trigger decisions ahead of
  time.)
- **The OneDrive AppFolder mirror is cut** — the mirror worker and the OneDrive storage backend
  are not ported. Mirroring a vault that is already a local folder is not worth the highest-risk
  component in the material absorbed. (ADR 0011, which carries the incident and the two
  feedback-loop fixes so a reviver does not rediscover them.)
- **No query outbox.** Write-back is the only outbox shape here; read-through query intents are
  not ported.
- **No operator policy.** Relationship-tier thresholds, inbox-triage rules, and note-type
  vocabularies are the configuring operator's policy, not this package's. If a heuristic cannot
  be expressed as config, it does not ship — it stays with the consumer and the skill exposes the
  seam instead.
- **Not a note editor.** The package writes what it extracts and what an intent asks for. It
  never rewrites markdown it did not produce; authored content is indexed, not restructured.
- **No Terraform, in any form.** Bicep for ARM resources, an idempotent Graph script for Entra
  objects. Mechanically enforced. (ADR 0003.)
- **Public release is a separate decision.** The package must be *fit* to publish; shipping it is
  a later call, not part of the work that made it self-contained.

## Principles

1. **Nothing assumed, everything configured.** No hardcoded folder name, tree shape, file-naming
   rule, threshold, model name, or dimension count anywhere. A module-level constant that an
   adopter would want to change is a defect.
2. **No defaults in signatures.** Every value comes from config or the caller. A missing required
   value crashes with a message naming the key and the file.
3. **Files are the source of truth; the index is derived and disposable.** Deleting the index and
   rebuilding must reproduce it.
4. **The knowledge half must not know Microsoft 365 exists.** `index/` never imports `m365/`.
   Enforced by `scripts/check_structure.py`, not by review. This is what keeps the knowledge
   layer independently useful and a future split cheap.
5. **A seam with one implementation gets an in-memory fake.** Every protocol ships one, and every
   test against the real implementation must pass against the fake unmodified. An unexercised
   protocol is decoration.
6. **Battle-tested code moves by path, not by rewrite.** The extractors and the locale-aware
   amendment classifier are ported; a behaviour difference is a port bug, not an improvement.
7. **Fail fast and loud.** A stale eTag raises rather than overwriting. A missing env var raises.
   A hook that raises is logged and does not abort the cycle — the one deliberate exception, and
   it is scoped to code the consumer supplied.
8. **The library never learns its consumers exist.** Everything consumer-specific attaches
   through config-declared hooks receiving a typed manifest. `scripts/check_publishable.py`
   rejects consumer vocabulary in CI.
9. **Structure is mechanical.** Allowed top-level directories, allowed subpackages, a 300-line
   module cap, import direction, and a test-presence map are all checked by a script that has
   itself been tested against planted violations.

## Status

- **Lifecycle:** development
- **Production gate:** false — not deployed-and-in-active-use; contracts are freely
  overhaul-able, no backwards-compatibility obligation. (Flip to `true` here and in `CLAUDE.md`
  only when the unit is deployed AND actively used.)

The goal above describes the unit's end state. Delivery is staged, and `CONTRACTS.md` marks each
surface as present or pending with the stage that delivers it:

| Stage | Delivers | State |
|---|---|---|
| Self-containment | rename, structure check, no-consumer-vocabulary check, own backlog, these artifacts | in progress |
| Knowledge layer | config root, model, parsers, index backends, vector/hybrid search, incremental sync, graph query, file catalog | delivered |
| M365 platform | vault contract, deletion lifecycle, outbox, reconciliation, Graph file ops, auth profiles, one transport | delivered |
| Runtime & CLI | scheduler, change manifest, hooks, full CLI verb set, indexing in the cycle, bundled skills | delivered |

Extraction, storage backends, document conversion, config loading, the admin UI, and the Azure
Bicep were present before staging began.

### Runtime & CLI — what landed

- **Scheduling** is persisted cursors and two pure functions (`schedule.py`), not a library that
  counts from process start. The index is a unit in the same schedule as the eight extractors
  (ADR 0021).
- **The change manifest** (`manifest.py`) is assembled by wrapping the storage backend, so it
  equals what the cycle wrote rather than what an extractor remembered to declare. Merge-store
  extractors additionally declare the record ids they merged (ADR 0020).
- **Hooks** (`hooks.py`) resolve at startup and fail soft at dispatch — logged, recorded,
  non-aborting, and still a failed cycle.
- **One cycle** (`cycle.py`, `index_step.py`) with a stated partial-failure policy: a failing unit
  never stops the others, and every failure reaches the manifest.
- **The CLI** (`cli.py`, `commands/`) is the whole operating surface. `scripts/independence_check.sh`
  and `tests/integration/test_independence.py` drive config → auth → run → search → push →
  reconcile → status from a scratch directory, which is the stage's acceptance gate.
- **Three bundled skills** (`skills/m365-brain-*`), package-prefixed and environmentless (ADR
  0022), validated against the agentskills.io specification's own tool.

Deferred, deliberately, and named here rather than left as a gap: no deployed service, no
multi-writer locking, no hook timeouts or subprocess isolation, no `index validate|delete|move`
(the index has no such operation to expose), and no catalog `extract` verb (nothing populates the
catalog yet). Static type checking is filed in the backlog and runs after the port settles.
