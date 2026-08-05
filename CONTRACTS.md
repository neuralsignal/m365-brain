---
title: "Contracts: m365-brain"
type: contracts
permalink: contracts-m365-brain
version: "0.1"
tags:
  - contracts
---

# Contracts: m365-brain

> Data contracts, public interfaces, and invariants for this unit. Language-agnostic statement of
> the surface; in Python, Pydantic models are the executable expression of these contracts.
> Freely overhaul-able while `production_gate: false` in `INTENT.md`.

**Present vs pending.** This package is mid-consolidation. Every surface below is marked
**Present** (implemented and tested today) or **Pending → \<stage\>** (specified, not yet
implemented, with the stage that delivers it). A contract stated as fact when it is aspiration is
worse than an admitted gap, so the distinction is carried on every clause rather than in a
preamble. Stages are the ones listed in `INTENT.md` § Status and tracked in `.trellis/`.

## Inbound contracts

### The config file — the primary contract

**Present.** One or more YAML paths, comma-separated, passed to the CLI. Multiple paths are
deep-merged left to right (dicts merge recursively; scalars and lists are replaced). `${VAR}`
references are expanded from the environment at load time and **a missing variable raises** —
there is no fallback value. Relative values of the path keys `base_path`, `db_path`,
`state_file_path`, and `token_cache_path` are resolved against the *config file's* directory, not
the process working directory.

Validated against frozen, strict Pydantic models. A missing or mistyped key raises `ConfigError`
naming the key. No section, and no field within a section, has a default in a function signature.

Sections present today:

| Section | Carries |
|---|---|
| `auth` | `client_id`, `tenant_id`, `scopes`, `token_cache_path`, optional `client_secret` |
| `service` | `mode`, `log_level`, `json_logs`, `continuous_poll_seconds`, `max_consecutive_auth_failures` |
| `storage` | `backend` (`local` \| `azure_blob`) plus the matching sub-block |
| `graph` | `max_retries`, `backoff_base_ms`, `timeout_seconds`, `max_pages`, `max_retry_after_seconds`, `error_message_max_length` |
| `state` | `state_file_path` |
| `extractors` | one required block per extractor: `enabled`, `poll_interval_minutes`, and per-extractor options |
| `converters` | conversion backends per file type, extraction limits, media options, slug/hash lengths |
| `web`, `worker` | optional; required only by the admin UI and the multi-user worker |

Also **Present**: `vault` (layout, per-extractor directory names, filenames); `auth.profiles`
(N named Entra apps, each with its own scopes and token cache); `outboxes`
(`attachment_root`, `forbidden_send_scopes`, `definitions.<name>.{tier,auth_profile}`,
`email.signature.{html_path,logo_path,logo_content_id}`, `reconcile.quote_markers`);
`m365.upload` (`inline_attachment_max_bytes`, `simple_upload_max_bytes`, `chunk_bytes` — which
must be a multiple of 320 KiB, and the validator caught that the constant it replaced was not).

Sections **Pending**: `index` (backend selection, roots, exclusions, vector settings) →
knowledge-layer stage; `hooks.post_cycle` → runtime stage.

### Microsoft Graph

**Present.** Graph v1.0, delegated permissions only, acquired by MSAL device-code flow (CLI) or
authorization-code flow (admin UI). No application permissions are required or requested. The
scope set is config, and the package requests only what the enabled extractors need — an
ungranted scope in the list blocks the entire login, so the list is not "everything just in
case". `README.md` carries the scope → extractor table.

Upstream shape the extractors depend on: delta endpoints and `@odata.nextLink` /
`@odata.deltaLink` pagination, `@removed` markers for upstream deletion, `429`/`5xx` with
`Retry-After`.

### Configured index roots

**Pending → knowledge-layer stage.** An explicit list of directories in config, each with a
recursion flag, plus exclusion globs. Roots are arbitrary markdown trees; there is **no
auto-discovery and no convention-based scanning**, because implicitness is the failure mode this
package exists to avoid. Indexing markdown the package did not produce is a first-class case, not
a side effect.

### Intents

**Present.** **Markdown with YAML frontmatter** — not JSON — written into
`<layout.outbox>/<outbox name>/<uuid>.md` by any caller. The frontmatter is the envelope, the
markdown body is the payload's `body` field. Rationale and the differences from the schema this
was ported from: ADR 0018.

```yaml
uuid: 5f2c…            # client-supplied idempotency key; MUST equal the filename stem
schema_version: 1
created_at: 2026-08-05T09:00:00Z
created_by: inbox-responder
payload:
  kind: email.draft    # the discriminator
  …
```

Envelope fields: `uuid` (1–64 chars), `schema_version`, `created_at`, `created_by` (1–128), and
`payload`. There is no `outbox` field and no `integration` field — `payload.kind` is the single
source of both.

Payload kinds and their required fields (`m365_brain/vault/payloads.py` is authoritative):

| `kind` | Fields |
|---|---|
| `email.draft` | `mailbox`, `to`, `cc`, `bcc`, `subject`, `attachments`, `inline_images`, `include_signature`, `revises_message_id` |
| `email.reply` | `mailbox`, `in_reply_to`, `reply_all`, `cc`, `attachments`, `inline_images`, `include_signature`, `revises_message_id` |
| `email.forward` | `mailbox`, `in_reply_to`, `to`, `cc`, `attachments`, `inline_images`, `include_signature`, `revises_message_id` |
| `teams.post_message` | `team_id`, `channel_id` |
| `file.update` | `site_hostname`, `site_path`, `library_name`, `item_path`, `etag`, `content_type` |

Every payload also takes its `body` from the markdown. Validation is `extra="forbid"` **and**
`strict=True`: an unknown field is a rejection, `reply_all: "true"` is a rejection, and a
`X | None` field carries **no default** — it is a required key that must be spelled `null`.

Hard parse errors, each of which was a real defect in the ported schema:

1. a frontmatter `body:` key, or `payload.body:` — the body comes from the markdown and nowhere else;
2. `uuid` ≠ filename stem — the two are one identity;
3. `in_reply_to` in legacy MAPI EntryID form (`0{8,}[0-9A-F]{16,}`) — re-ingest the source message.

`include_signature` is the polarity flip of the previous pipeline's `skip_signature`:
`include_signature = not skip_signature`. It has no default, so every intent states it.

## Outbound contracts

### The vault tree

**Present** for the directory contract, the path builder, and the per-extractor markdown output
underneath it. The outbox subtree is **Pending → outbox stage**; its path methods exist and are
tested, but nothing writes intents yet.

```
<vault.root>/
├── <layout.inbox>/        read-only upstream truth, written only by extractors
│   └── <extractor_dirs.*>/   one subtree per extractor, names from config
├── <layout.annotations>/  agent-authored overlays alongside, never inside, inbox
├── <layout.outbox>/       typed intents, one directory per outbox name
└── <layout.meta>/         sync state, manifests, and the intent archive
    ├── <layout.inflight>/    claimed, outcome unknown — never auto-retried
    ├── <layout.processed>/   dispatched intents + receipt sidecars
    └── <layout.rejected>/    blocked or failed intents + receipt sidecars
```

Every segment in that tree is a config value. `m365_brain/vault/paths.py` is the only thing that
builds one; a hardcoded directory or filename anywhere under `m365_brain/vault/` or
`m365_brain/m365/` defeats the contract and is grep-checked.

Paths are **storage-relative POSIX keys**, never filesystem paths — `<vault.root>` above is the
storage backend's own `base_path`/`prefix` and is not repeated inside the key.

### Markdown files

**Present.** UTF-8 markdown with a YAML frontmatter block. Frontmatter fields are per entity type
(email, event, chat message, channel message, file, contact, directory user). Filenames are
slugged and hash-suffixed with lengths taken from `converters.slug_max_length` and
`converters.hash_length`. Attachments and converted derivatives are written beside the message
that carries them.

Downstream consumers may rely on: the file being valid UTF-8 markdown; the frontmatter parsing as
YAML; the path being stable for a given upstream item id; and the file disappearing when the
upstream item does.

### Sync state

**Present.** JSON at `state.state_file_path` (per-`(user, extractor)` files under the multi-user
worker). Holds delta tokens and last-run timestamps. It is bookkeeping, not data: deleting it
forces a full re-pull, never a data loss.

Every extractor's state carries **`path_map`** (`{upstream id: storage path}`), the key defined by
`m365_brain.vault.removal.PATH_MAP_STATE_KEY`. It is what makes an upstream deletion actionable:
without a way back from an id to the file written for it, there is nothing to delete. The two file
extractors keep the same map under their pre-existing `file_paths` / `file_paths_{site}_{drive}`
keys. Deleting the state therefore forfeits pending deletions until the next full re-pull, which
rewrites the map.

**Removal coverage is partial, and this is a permissions limit rather than an omission:**

| Extractor | Upstream signal | Handled |
|---|---|---|
| `email` | delta `@removed` | yes |
| `onedrive`, `sharepoint` | delta `@removed` | yes |
| `contacts` | delta `@removed` | yes |
| `calendar` | `isCancelled: true` | yes |
| `directory` | delta `@removed`, and `accountEnabled: false` | yes — the `$filter=accountEnabled eq true` server-side filter was **dropped** so the disable is visible at all; a filtered-out user simply vanished from results and its page stayed forever |
| `teams_chats` | none | **no** |
| `teams_channels` | none | **no** |

The two Teams extractors have no upstream removal signal available: there is no Graph delta
endpoint for chats or channels under delegated permissions, which is also why they sync by
watermark rather than by delta. They keep a `path_map` regardless — `vault purge` uses it, and a
future signal would need it — but a chat deleted upstream persists until the extractor is purged.

The recorded value in `path_map` is a **prefix, not always a file**. Extractors whose item is a
directory (email, contacts, directory) record the directory, so removal takes the entry file *and*
the attachments beside it; the rest record the single markdown file they wrote. Removing only the
markdown would leave attachment blobs orphaned under an unreferenced directory.

### Change manifest

**Pending → runtime stage.** A typed record per cycle of what was created, updated, and deleted,
per extractor, with errors, persisted under the configured meta directory. It is the contract
hooks consume, and it replaces filesystem re-scanning — the manifest *is* the watermark, so a
consumer keeping its own seen-set file is doing work the manifest already did.

### The intent archive

**Present.** Dispatch is claim → route → execute → receipt → archive, and the archive is the
**ledger**: `already_dispatched(uuid)` is true once either archive holds the uuid, so a replayed
intent is skipped and a rejected one is not retried. Purging `<layout.processed>` re-arms replay —
a deliberate operator act, not something guarded against.

```
<meta>/<inflight>/<uuid>.md              claimed, outcome unknown — never auto-retried (ADR 0017)
<meta>/<processed>/<uuid>.md             the intent, byte-identical to what was submitted
<meta>/<processed>/<uuid>.receipt.json   DispatchReceipt
<meta>/<processed>/<uuid>.reconciled.json  terminal verdict, written by the reconcile pass
<meta>/<rejected>/<uuid>.md              + <uuid>.receipt.json
```

`DispatchReceipt`: `uuid`, `kind`, `outcome` (`dispatched` | `rejected` | `blocked`),
`dispatched_at`, `graph_message_id`, `reason`, `detail`. `reason` is a **closed set** —
`tier_blocked`, `no_approval_recorded`, `etag_conflict`, `graph_error`, `attachment_missing`,
`parse_error`, `unknown_outbox` — because an operator greps by it. See ADR 0019.

### Permission tiers

**Present.** A tier is a property of the **outbox**, read from `outboxes.definitions.<name>.tier`,
never from the intent file.

| tier | `pending` | `approved` | terminal |
|---|---|---|---|
| `never_auto` | `await_admin` → `blocked`, `reason: tier_blocked` | *raises* | `archive` |
| `human_approval` | `await_approval` → `rejected`, `reason: no_approval_recorded` | `execute` | `archive` |
| `draft_only` | **`execute`** (ADR 0013) | *raises* | `archive` |
| `auto_send` | `execute` | *raises* | `archive` |

Two guards run at registry build — process start, not per intent — and both crash the process:

1. a `draft_only` outbox whose handler declares an operation outside
   `{create_draft, update_draft, attach}`;
2. a `draft_only` outbox whose auth profile is granted a scope in
   `outboxes.forbidden_send_scopes`.

A *data* item whose tier forbids dispatch is not an exception: it is a receipt with an outcome and
a reason, and the pass continues.

### Writes into Microsoft 365

**Present.** Email drafts via `createReply` / `createReplyAll` / `createForward` — never `/reply`
or `/forward`, and `Mail.Send` is not among the requested scopes. Teams channel posts
(delegated-only; `ChannelMessage.Send` has no application variant). File writes via `create_file`
or `update_file`; **there is no unconditional-write function** (ADR 0016).

The reply/forward flow is three requests and must stay three: `POST create*` → `GET` the stub →
`PATCH` the merged body. The middle call reads back the quoted original Graph generated; collapsing
the sequence drops the quote silently.

### The index

**Pending → knowledge-layer stage.** A SQLite database at the configured path (FTS5, WAL,
busy-timeout, readonly mode available). **Derived and disposable**: it is not a system of record,
carries nothing that cannot be recomputed from the markdown, and is safe to delete.

## Public interfaces

### CLI

**Present today:**

| Verb | Behaviour |
|---|---|
| `--config <paths>` | global, required; comma-separated, deep-merged |
| `auth login` | device-code flow, caches the token |
| `auth status` | cached account, tenant, token validity, scopes |
| `sync --once` | run all enabled extractors once |
| `sync --dry-run` | validate auth and probe each extractor, writing nothing |
| `sync --extractors a,b` | restrict to named extractors |
| `worker` | multi-user per-`(user, extractor)` job loop (requires the `web` config section) |

**Pending → runtime stage** — the target verb set, which supersedes `sync`:

`init` · `auth login|status --profile` · `run [--once|--only|--resync|--delay-start]` ·
`index sync|search|context|recent` · `outbox push|reconcile` · `files pull|push` · `teams post` ·
`catalog` · `validate` · `status`.

The console script is `m365-brain`, with `mb` as a short alias. The promise the CLI makes is that
operating the package requires a config file and these verbs — never code written by the
operator.

### Protocols

The seam set. One implementation each today, each shipping an in-memory fake (ADR 0010).

| Protocol | Module | State | First implementation |
|---|---|---|---|
| `StorageBackend` | `m365_brain/storage/base.py` | **Present** | local filesystem, Azure Blob |
| `IndexBackend` | `m365_brain/index/backends/base.py` | Pending → knowledge-layer | SQLite + FTS5 |
| `EmbeddingProvider` | `m365_brain/index/vector/base.py` | Pending → knowledge-layer | fastembed |
| `VectorStore` | `m365_brain/index/vector/base.py` | Pending → knowledge-layer | sqlite-vec |
| `IntentStore` | `m365_brain/outbox/stores.py` | **Present** | `FilesystemIntentStore` + `InMemoryIntentStore` |
| `OutboxHandler` | `m365_brain/vault/dispatch.py` | **Present** | `EmailOutbox`, `TeamsPostOutbox`, `FileUpdateOutbox` |
| `StateStore` | `m365_brain/state.py` | Pending → M365-platform | JSON under the meta directory |

`StorageBackend` today: `write_file(path, content)`, `read_file(path)`, `file_exists(path)`,
`list_files(prefix)`, `delete_file(path)`, `write_bytes(path, content)`. All paths are relative to
the backend root.

### Library API

**Present.** `m365_brain.config.load_config(paths) -> Config`; `m365_brain.storage.create_storage`;
`m365_brain.state.SyncState`; `m365_brain.sync.run_extractors(config, token_provider, storage,
state, names)` plus `build_context(config, storage)` and the `EXTRACTORS` registry;
`m365_brain.m365.client.GraphClient` for a retrying, paginating, *writing* Graph client;
`m365_brain.vault.paths.VaultPaths` for every path in the vault.

`GraphClient` exposes `get` / `get_bytes` / `get_bytes_with_content_type` / `get_pages` /
`get_paginated` / `get_delta`, and the write verbs `post` / `patch` / `put_bytes`. All of them
traverse one retry, backoff and 401-refresh policy whose thresholds come from `graph:` in config.
`GraphNotFoundError` (404) and `GraphConflictError` (412) subclass `GraphApiError` and are raised
before the retry branch, so an existing `except GraphApiError` still catches them and a conditional
write never degrades into an unconditional one.

Every extractor takes `run(client, storage, state, config, ctx)` where `ctx` is an
`ExtractorContext(paths, converters, removal)`. One shape for all eight — `EXTRACTORS` no longer
carries a `needs_converters` flag, because there is no longer a second call shape to dispatch to.

**Present** for the outbox half:

- `m365_brain.m365.auth.profiles.AuthProfiles(profiles)` — `provider(name)`, `login(name)`,
  `status(name)`, `scopes(name)`. One MSAL app and one token cache per named profile; two profiles
  sharing a `token_cache_path` is refused at construction.
- `m365_brain.m365.files` — `resolve_site_id`, `resolve_drive_id`, `resolve_default_drive_id`,
  `list_children`, `get_file`, `item_etag`, `download_file_bytes`, `create_file`, `update_file`.
- `m365_brain.m365.outboxes.build_handlers(outboxes_config, upload_config, clients)` — one handler
  per configured outbox, each bound to the `GraphClient` of the auth profile its config names.
- `m365_brain.outbox.build_registry(outboxes_config, auth_profiles, handlers)` — handlers are
  **injected**, so no module imports both `outbox` and `m365` (ADR 0014).
- `m365_brain.outbox.runner.push(store, registry, router) -> PushCounts` and
  `reconcile(store, fetch, markers) -> list[ReconcileOutcome]`. The reconciliation Graph fetch is a
  `(mailbox, message_id, select) -> dict | None` callable supplied by the caller.

`ReconcileOutcome` carries `uuid`, `verdict` (`sent` | `amended` | `rejected` | `pending`),
`graph_message_id`, `conversation_id`, `sent_at`, `sent_body_html`, `original_body`. The sent HTML
and the original body travel **by value**, so no knowledge-base path crosses the boundary in either
direction; `post_reconcile` hooks do the projection.

**Pending → knowledge-layer stage.** `m365_brain/workspace.py` — the facade that opens a config
and returns a working handle. It is the API the bundled skills, the CLI, and migrating call sites
target, and it is the intended entry point once it exists.

### Bundled skills

**Pending → runtime stage.** `skills/{knowledge,files,ops}` in agentskills.io format, as thin
wrappers over the CLI and the facade. Their contract to an adopter: every threshold and rule they
apply is traceable to a config key (ADR 0008).

## Invariants

1. **Nothing under `inbox/` is written by anything but an extractor.** Annotations, intents, and
   manifests live in their own siblings. This is what makes `inbox/` re-derivable: it can be
   deleted and re-pulled without losing authored work. *(Present.)*

   Corollary, also **Present**: `StorageBackend.delete_file` is idempotent. Both backends already
   swallow a missing path, and `RemovalHandler` relies on it — upstream re-sends a `@removed`
   marker for an id it has already sent one for, and a second pass must be a no-op rather than a
   404. A backend that raised on a missing path would turn every repeated removal into a failed
   sync cycle.
2. **A stale eTag raises rather than overwriting.** `update_file` sends `If-Match`; a `412` is a
   loud failure, never a retry-with-force. This is the only silent-data-loss path in the package
   and it is tested explicitly — on the mock's call log, because "it raised" is not the property,
   "it did not write" is. Structural rather than behavioural: no public function in
   `m365_brain/m365/files.py` takes a nullable `if_match`, so there is no unconditional-write path
   to call. *(Present — ADR 0016.)*
3. **Every configurable value comes from config; there is no code default.** No module-level
   constant that an adopter would want to change — no dimension count, embedding model name,
   chunk size, interval, threshold, or directory name. *(Present for the config sections that
   exist; extended by each stage.)*
4. **A missing required value crashes, naming the key.** Nothing is silently substituted, and a
   missing `${VAR}` is an error rather than an empty string. *(Present.)*
5. **Files are the source of truth; the index is derived.** Deleting the index and rebuilding
   reproduces it; the index never holds the only copy of anything. *(Pending → knowledge-layer.)*
6. **Re-running is safe.** Extraction, indexing, intent push, and reconciliation are idempotent:
   a second run over unchanged input produces no additional side effect. Intents carry a
   client-supplied idempotency key so a retried push does not duplicate a draft. *(Present for
   extraction and the outbox; pending for indexing.)*

   Corollary, also **Present**: an intent claimed with no recorded outcome is **never
   auto-retried**. Repeating a send whose result is unknown duplicates mail, which is the failure
   an outbox exists to prevent. See ADR 0017, including the named non-atomicity of the claim.
7. **Upstream deletion propagates exactly once.** One canonical removal handler maps an upstream
   id to its written path, hard-deletes, and drops the state-map entry, so a repeated `@removed`
   does not re-404. *(Pending → M365-platform.)*
8. **Email outboxes are `draft_only`.** They create drafts for human review; `Mail.Send` is not
   requested, so auto-send is impossible by permission, not merely by policy — and a config that
   grants it anyway fails at process start rather than at dispatch. *(Present — ADR 0013.)*
9. **`index/` never imports `m365/`.** A same-layer import between the two halves is a CI
   failure. The knowledge layer must work end to end on ordinary markdown with no Microsoft 365
   present. *(Enforced now by `scripts/check_structure.py`.)*
10. **No module exceeds 300 lines, and no module lacks a test file.** Both are checked, and the
    checker is itself tested against planted violations. *(Present.)*
11. **A local storage path may not escape its root.** Path traversal is rejected in the local
    backend; the MSAL token cache is written `0600`. *(Present.)*
12. **A hook that raises is logged and the cycle completes.** The single deliberate exception to
    fail-loud, scoped to consumer-supplied code so one consumer's bug cannot wedge extraction.
    *(Pending → runtime stage.)*
13. **No consumer vocabulary appears anywhere in the repo.** Enforced by
    `scripts/check_no_workspace.py` over all tracked text, in pre-commit and CI. *(Present.)*

## Pydantic / schema source

Authoritative for field-level detail; this document summarises.

| Contract | Source |
|---|---|
| Config root and every section | `m365_brain/config/schema.py` — `Config` and its frozen, strict sub-models |
| Config loading, merge, env expansion, path resolution | `m365_brain/config/loader.py`; errors in `m365_brain/config/errors.py` |
| Storage seam | `m365_brain/storage/base.py` |
| Sync state on disk | `m365_brain/state.py` |
| Markdown frontmatter per entity type | `m365_brain/frontmatter/` |
| Admin/worker database tables | `m365_brain/models.py` (SQLModel), migrated by `alembic/` |
| Knowledge model — `Entity`, `Observation`, `Relation` | `m365_brain/model.py` — *pending → knowledge-layer stage* |
| Index, vector, and embedding seams | `m365_brain/index/backends/base.py`, `m365_brain/index/vector/base.py` — *pending → knowledge-layer stage* |
| Intent envelope and per-outbox payload schemas | `m365_brain/vault/intent.py` and `m365_brain/vault/payloads.py` (ADR 0014) |
| Dispatch vocabulary — `GraphOp`, `DispatchResult`, `DispatchReceipt`, `OutboxHandler` | `m365_brain/vault/dispatch.py` |
| Tier state machine | `m365_brain/outbox/tiers.py`; guards in `m365_brain/outbox/registry.py` |
| Reconciliation outcome | `m365_brain/outbox/reconcile.py` |
| Change manifest | `m365_brain/manifest.py` — *pending → runtime stage* |

Structural contracts that are not Pydantic — allowed directories, import direction, module size,
test presence — live in `scripts/check_structure.py`, which is the executable statement of the
repository layout in the same way the models are of the data.
