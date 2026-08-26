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

**Present vs pending.** Every surface below is marked **Present** (implemented and tested
today) or **Pending → \<stage\>** (specified, not yet implemented, with the stage that delivers
it). A contract stated as fact when it is aspiration is worse than an admitted gap — and the
converse rots the same way: the index, the hooks, the change manifest, the `StateStore` and the
`workspace.py` facade all stayed marked **Pending** here for a stage after they shipped, so this
document was understating a package it was supposed to describe. A reader deciding whether to
depend on the index was told it did not exist yet while it was answering queries in production.
Stages are the ones listed in `INTENT.md` § Status and tracked in `.trellis/`.

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
| `service` | `log_level`, `json_logs`, `continuous_poll_seconds`, `max_consecutive_auth_failures` |
| `storage` | `backend` (`local` \| `azure_blob`) plus the matching sub-block |
| `graph` | `max_retries`, `backoff_base_ms`, `timeout_seconds`, `max_pages`, `max_retry_after_seconds`, `error_message_max_length` |
| `extractors` | one required block per extractor: `enabled`, `poll_interval_minutes`, and per-extractor options |
| `converters` | conversion backends per file type, extraction limits, media options |
| `web`, `worker` | optional; required only by the admin UI and the multi-user worker |

Also **Present**: `vault` (layout, per-extractor directory names, filenames); `auth.profiles`
(N named Entra apps, each with its own scopes and token cache); `outboxes`
(`attachment_root`, `forbidden_send_scopes`, `definitions.<name>.{authority,auth_profile}`,
`email.signature.{html_path,logo_path,logo_content_id}`, `reconcile.quote_markers`);
`m365.upload` (`inline_attachment_max_bytes`, `simple_upload_max_bytes`, `chunk_bytes` — which
must be a multiple of 320 KiB, and the validator caught that the constant it replaced was not).

`ops.triage.fields` names the seven things a message corpus is read through — `entity_type` plus the
`folder`, `conversation_id`, `message_id`, `sender`, `recipients` and `timestamp` observation
categories. All seven are required and none has a code default, for a reason the other required keys
do not share: a wrong threshold produces a visibly wrong report, whereas a guessed category name
produces an empty one, and an empty triage report is indistinguishable from an inbox with nothing
owing. The `--*-category` options on `ops triage` override one for a single run and are the only
place a CLI flag may restate a configured value — a category is a statement about which corpus is
being read, not a policy the config owns.

`conversation_id` and `message_id` are **two identifier spaces and both are read**: a reply is
paired with the message it answers by conversation, while an intent's `in_reply_to` names a single
message. Comparing one against the other is not a near miss — it is a clause that cannot fire.

`ops.tiers.interaction_sources` carries the same kind of name and the same hazard. Every
`entity_type`, `party_from` and `timestamp` in it is a statement about what the corpus contains, so
a source naming something no producer writes reports zero counterparties, which reads as a quiet
quarter rather than as a fault. The shipped sources are checked against the bundled builders' real
output, per source, in `tests/unit/test_ops.py`.

Also **Present**: `index` (backend selection, roots, exclusions, search and vector settings, the
catalog vocabulary) and `hooks.post_cycle` / `hooks.post_reconcile`. Both were listed here as
pending for a stage after they shipped.

**There is no `state:` section.** It was documented above for two stages after the key was gone.
State lives under the vault's meta directory, addressed through `vault.layout.state`, so the store
moves with the vault it belongs to instead of being a second root to keep in step.

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

**Present.** An explicit list of directories in config, each with a recursion flag, plus
exclusion globs. Roots are arbitrary markdown trees; there is **no
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

**Present**, the outbox subtree included: `teams post` writes an intent, `outbox push` claims and
dispatches, and `outbox reconcile` records what became of it.

```
<vault.root>/
├── <layout.inbox>/        read-only upstream truth, written only by extractors
│   └── <extractor_dirs.*>/   one subtree per extractor, names from config
├── <layout.annotations>/  agent-authored overlays alongside, never inside, inbox
├── <layout.outbox>/       typed intents, one directory per outbox name
└── <layout.meta>/         sync state, manifests, and the intent archive
    ├── <layout.inflight>/    claimed, outcome unknown — never auto-retried
    ├── <layout.processed>/   dispatched intents + receipt sidecars
    └── <layout.failed>/      blocked or failed intents + receipt sidecars
```

Every segment in that tree is a config value. `m365_brain/vault/paths.py` is the only thing that
builds one; a hardcoded directory or filename anywhere under `m365_brain/vault/` or
`m365_brain/m365/` defeats the contract and is grep-checked.

Paths are **storage-relative POSIX keys**, never filesystem paths — `<vault.root>` above is the
storage backend's own `base_path`/`prefix` and is not repeated inside the key. That holds inside
the library and on disk. It stops at the **process boundary**: `m365_brain/storage/__init__.py`
carries `resolve_key` / `storage_key`, the one pair that adds the base and takes it away again,
and the CLI's `emit` applies it to everything it prints. See the output contract under CLI.

### Markdown files

**Present.** UTF-8 markdown with a YAML frontmatter block. Frontmatter fields are per entity type
(email, event, chat message, channel message, file, contact, directory user). Filenames are
slugged and hash-suffixed at a fixed 80 characters and 6 hex digits. Those were once
`converters.slug_max_length` / `.hash_length`; the keys were declared, required, and read by
nothing — all fifteen call sites passed the literals — so they are gone. Attachments and converted derivatives are written beside the message
that carries them.

Downstream consumers may rely on: the file being valid UTF-8 markdown; the frontmatter parsing as
YAML; the path being stable for a given upstream item id; and the file disappearing when the
upstream item does.

**A fact an operation has to read is written as a scalar.** `m365_brain/parsers/document.py`
promotes a scalar frontmatter key to an observation and leaves a list or a dict in metadata, and
metadata is not retrievable per entity — `EntityRef` carries none, and `IndexBackend` offers only a
filter over it. A structure is therefore not a formatting choice but an invisible fact. The email
builder consequently carries Graph's `conversationId` through as a `conversation_id` key and its
`id` as a `message_id` key — duplicating `source.id`, which is inside a dict and so unreadable —
and writes `to` as a comma-joined string rather than the list Graph returns;
`ops.names.email_addresses` splits the addresses back out. `source` and `tags` are the two
deliberate structures, and nothing reads them out of the index.

**A fact with N values is written as N body relations**, because a scalar cannot hold them and
joining them into one string makes them one value. An event's attendees are the case: the calendar
builder emits `attendees` as a list for a reader *and* one `- attended_by [[Name]]` line per
attendee through `attendee_relations`, which is what `ops tiers` counts. Joining the names instead
— the repair that was right for an email's `to` line — would have produced a single counterparty
called "Ana Ruiz, Bo Frey", because `ops tiers` groups on the whole observation.

A Teams chat's participants are the same fact and take the same shape: `participants` stays a list
for a reader, and `participant_relations` writes one `- participant [[Name]]` line per person. The
link names the participant as Graph spelled them rather than a `contact-<slug>` placeholder —
`ops.link_resolution.unresolved_prefix` is the spelling for a link that *cannot* resolve, and it
also becomes the counterparty `ops tiers` reports, so a slug would keep one person seen in a chat
and on an event from being one counterparty.

A **channel** carries no such fact. `TeamsChannelData` states a team and a channel, never people;
its senders exist only as rendered prose in the message timeline, which is neither an observation
nor a relation. There is therefore no `teams_channel` interaction source in the shipped template,
and adding one would report zero counterparties — indistinguishable from a quiet quarter.

A contact's addresses are the same fact in the other shape. `email` is a list for a reader, and
`address_observations` writes one `- [email] a@example.com` line per address — an **observation**
rather than a relation, because an address is an attribute of the person and not an edge to another
entity. `ops links` is the reader: it looks an address up out of a candidate's observations, by
shape rather than by category name, so what a producer owes it is that the address reach an
observation at all. A **directory user** has one address, so its scalar `email` key is promoted
already and no body line is added — a second spelling of a fact the index holds is duplication,
not a repair.

A directory user's **org edges are readable but untyped**, and are left that way deliberately.
`- **Manager:** [[…]]` parses as an explicit relation whose *type* is the literal `**Manager:**`,
and each direct-report sub-bullet parses under `index.relations.explicit_default_type`
(`relates_to`). Both reach the index and both resolve, so neither is silent — nothing in this
family is wrong here. Nothing reads them either, so naming them properly today would be a
vocabulary with no consumer. The trap to know about: the first config that wants to count managers
will spell `manager`, not `**Manager:**`, and get an empty report. Give the edges named types at
that point, in the same move that adds the reader.

**A config key naming a type or category names one a producer writes.** `ops.link_resolution`
`target_types` is a list for that reason: a person is `contact` from one bundled extractor and
`directory_user` from the other, and a single name would silently exclude half a corpus. The same
rule governs `ops.tiers.interaction_sources` and `ops.triage.fields` — a name no producer writes
yields an empty report, which reads as a clean corpus rather than as a defect.

### Sync state

**Present.** JSON under `<vault.root>/<layout.meta>/<layout.state>/`, one file per namespace
(per-`(user, extractor)` files under the multi-user worker). Holds delta tokens and last-run
timestamps. It is bookkeeping, not data: deleting it
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

**Present.** A typed record per cycle of what was created, updated, and deleted,
per extractor, with errors, persisted under the configured meta directory. It is the contract
hooks consume, and it replaces filesystem re-scanning — the manifest *is* the watermark, so a
consumer keeping its own seen-set file is doing work the manifest already did.

### The intent archive

**Present.** Dispatch is claim → route → execute → receipt → archive, and the archive is the
**ledger**: `already_dispatched(uuid)` is true once either archive holds the uuid, so a replayed
intent is skipped and a failed one is not retried. Purging `<layout.processed>` re-arms replay —
a deliberate operator act, not something guarded against.

```
<meta>/<inflight>/<uuid>.md              claimed, outcome unknown — never auto-retried (ADR 0017)
<meta>/<processed>/<uuid>.md             the intent, byte-identical to what was submitted
<meta>/<processed>/<uuid>.receipt.json   DispatchReceipt
<meta>/<processed>/<uuid>.reconciled.json  terminal verdict, written by the reconcile pass
<meta>/<failed>/<uuid>.md                + <uuid>.receipt.json
```

`DispatchReceipt`: `uuid`, `kind`, `outcome` (`dispatched` | `failed` | `blocked`),
`dispatched_at`, `graph_message_id`, `reason`, `detail`. `reason` is a **closed set** —
`tier_blocked`, `no_approval_recorded`, `etag_conflict`, `graph_error`, `attachment_missing`,
`parse_error`, `unknown_outbox` — because an operator greps by it. See ADR 0019.

### Permission authorities

**Present.** An authority is a property of the **outbox**, read from
`outboxes.definitions.<name>.authority`, never from the intent file. It is not called `tier`:
`ops tiers` spends that word on a person's relationship rung.

| authority | `pending` | `approved` | terminal |
|---|---|---|---|
| `never_auto` | `await_admin` → `blocked`, `reason: tier_blocked` | *raises* | `archive` |
| `human_approval` | `await_approval` → `failed`, `reason: no_approval_recorded` | `execute` | `archive` |
| `draft_only` | **`execute`** (ADR 0013) | *raises* | `archive` |
| `auto_send` | `execute` | *raises* | `archive` |

Two guards run at registry build — process start, not per intent — and both crash the process:

1. a `draft_only` outbox whose handler declares an operation outside
   `{create_draft, update_draft, attach}`;
2. a `draft_only` outbox whose auth profile is granted a scope in
   `outboxes.forbidden_send_scopes`.

A *data* item whose authority forbids dispatch is not an exception: it is a receipt with an outcome and
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

**Present.** A SQLite database at the configured path (FTS5, WAL,
busy-timeout, readonly mode available). **Derived and disposable**: it is not a system of record,
carries nothing that cannot be recomputed from the markdown, and is safe to delete.

## Public interfaces

### CLI

The console script is **`m365-brain`, and only `m365-brain`** (ADR 0023). The promise the CLI
makes is that operating the package requires a config file and these verbs — never code written by
the operator.

`--config` is optional at the root group and required by every verb except `init`, which creates
the file and so cannot demand it exists. It accepts comma-separated paths, deep-merged left to
right.

**Output contract.** Results go to **stdout**; logs go to **stderr** through structlog. Every read
verb takes `--json`. A caller therefore never parses human text and never has to separate log noise
from data.

**A capped result says so.** Every verb taking a `--limit` — `index search`, `index recent`,
`index catalog list`, `index catalog search`, `index catalog extract` — emits `total`, `returned`
and `limit` beside its rows, and appends a `-- truncated: M of N` line to its human output when the
two differ. `returned < total` is the whole test, and it is one shape across all five. `--limit`
defaults to `index.search.page_size` everywhere rather than to a literal in a decorator, and
`--help` names that key. `catalog list --json` used to return exactly 100 of 900 rows in a bare
`{"entries": [...]}`, which is the `$top` defect again: a call that succeeds while returning a
fraction.

**Every path printed is fully resolved** — an absolute filesystem path under `storage.backend:
local`, a blob URL under `azure_blob`. It needs no base the caller has to know, so what one verb
prints the next verb takes and `pwd` decides nothing. The join happens once, in `emit`, which is
why a verb added later gets it without asking. Two payload fields stay relative and say so in the
same object: `vault path --json` `.relative` and `index paths --json` `.inbox.*` are printed beside
the root they hang off, so nothing is missing.

| Verb | Options | Prints | Exit |
|---|---|---|---|
| `init PATH` | `--vault DIR` (required) | every path created | 0 / 3 if PATH exists |
| `run` | `--once` · `--only a,b` · `--resync` · `--delay-start MIN` · `--json` | cycle summary; `--json` emits the manifest | 0 / 1 / 3 / 4 |
| `extract` | `--only a,b` · `--resync` · `--dry-run` · `--json` | per-extractor item counts | 0 / 1 / 3 / 4 |
| `status` | `--json` | per-unit last run / last success / failure streak; last cycle and its hooks | 0, 1 if anything is failing |
| `auth login` | `--profile NAME` (required) | device-code prompt, then account and scopes | 0 / 3 / 4 |
| `auth status` | `--profile NAME` · `--json` | state, accounts, scopes per profile | 0, 4 if any profile has no usable token |
| `config validate` | — | `ok`, the sections present, hooks resolved | 0 / 3 |
| `config show` | `--json` | the effective merged config, secrets redacted | 0 / 3 |
| `index sync` | `--root NAME` (repeatable) | indexed / skipped / pruned / errors / elapsed | 0 / 1 / 3 |
| `index rebuild` | `--root NAME` · `--yes` (required) | same counters | 0 / 1 / 3 |
| `index search` | `QUERY` · `--search-type text\|vector\|hybrid` · `--type` · `--tag` · `--field` · `--limit` (page size) · `--page` · `--json` | ranked results, with `total` / `returned` / `limit` | 0 / 3 |
| `index context` | `ENTITY` \| `--permalink` · `--depth` · `--format text\|json` | entity, observations, edges | 0 / 3 |
| `index recent` | `--timeframe` · `--type` (narrows the query, not the page) · `--limit` · `--json` | recently changed entities, with `total` / `returned` / `limit` | 0 / 3 |
| `index paths` | `--json` | configured roots, database, and each extractor's inbox | 0 / 3 |
| `index catalog list` | `--ext` · `--extractor` · `--status` · `--modified-after` · `--limit` · `--stats` · `--json` | catalog rows with `total` / `returned` / `limit`, or per-state counts | 0 / 3 |
| `index catalog search` | `QUERY` · `--status` · `--limit` · `--json` | matching rows, with `total` / `returned` / `limit` | 0 / 3 |
| `index catalog resolve` | `QUERY` — a file name, or a path this family printed · `--json` | one source path, resolved; ambiguity is an error | 0 / 3 / 5 |
| `index catalog read` | `PATH` — absolute, or relative to the vault root; never to the CWD | the converted markdown on stdout; writes nothing | 0 / 2 / 3 |
| `outbox list` | `--outbox NAME` · `--json` | intents with uuid, outbox, authority, status | 0 / 3 |
| `outbox push` | `--outbox NAME` · `--json` | dispatched / blocked / failed / replayed / contended / inflight | 0 / 1 / 3 / 4 |
| `outbox reconcile` | `--outbox NAME` · `--json` | per-intent verdict | 0 / 1 / 3 / 4 |
| `files pull` | `--profile` · `--site-hostname` · `--site-path` · `--library` · `--item-path` · `--out` · `--json` | bytes written and the eTag | 0 / 1 / 3 / 4 |
| `files push` | the same, plus `--in` · `--content-type` · `--if-match` (required) | the new eTag; **raises on 412, never overwrites** | 0 / 1 / 3 / 4 |
| `teams post` | `--channel-url` · `--body-file` · `--created-by` · `--json` | the intent file written; sends nothing | 0 / 3 |
| `vault path AREA` | `--extractor` · `--outbox` · `--json` | one path | 0 / 3 |
| `ops resolve-links` | `--json` | unresolved links and their candidates | 0 / 3 |
| `ops tiers` | `--json` | per-counterparty tier and staleness; reports only, there is no write-back to configure | 0 / 3 |
| `ops triage` | `--timeframe` · seven optional `--*-category` overrides of `ops.triage.fields` · `--json` | messages awaiting a reply | 0 / 3 |

**`worker` is not a verb.** It was tabulated here as one. `m365_brain/worker.py` is a library
module — the multi-user per-`(user, extractor)` job loop — and it carries no Click decorator,
`pyproject.toml` exposes one console script, and nothing registers it. It runs as a thread inside
the admin app (`worker.start_worker_thread`). **`docker-compose.yaml`'s `worker` service still
invokes `m365-brain … worker` and would fail on start**; whether that becomes a registered verb or
the service goes is a deployment-shape decision, not a documentation one.

**Not present, and deliberately.** There is no `outbox new` — an intent *is* a markdown file in the
outbox directory, and a verb writing the same bytes would be a second way to do one thing.
`teams post` is the single exception, because a channel intent needs a `(team_id, channel_id)` pair
no human types from memory. There is no bare `validate`: `config validate` says what it validates.
There is no `index validate`, `index delete` or `index move` — the index has no schema-validation or
file-mutation operation to expose, and a CLI verb is not the place to invent one.

### Exit codes

| Code | Meaning | What the caller should do |
|---|---|---|
| 0 | success | — |
| 1 | operational failure: an extractor, the index step, a hook, a push or a reconcile failed | retry, or read the log |
| 2 | usage error (Click's own) | fix the command line |
| 3 | configuration invalid or unresolvable — bad YAML, a missing key, an unknown extractor / outbox / root / profile / area name, or a hook that cannot be imported | fix the config |
| 4 | authentication required or expired beyond refresh | `auth login --profile …` |
| 5 | the corpus holds nothing matching the query | widen the query, or sync |

3 and 4 exist so a supervisor can tell "you typed it wrong" and "go re-login" apart from "Graph is
down" without scraping a message. They are mapped in one place — the root group's `invoke` — rather
than in fifteen `try` blocks.

5 exists because 3 was answering two different questions. `index context` and `catalog resolve`
raised `ConfigError` when nothing matched, so a query against a perfectly valid config reported
"configuration invalid" and sent the reader to edit a file that was fine — while `catalog search`
exited 0 for the same situation. Only the verbs that promise **exactly one** answer raise 5; the
list-shaped verbs (`search`, `list`) still exit 0 with an empty result, because "no rows" is an
ordinary answer to a search.

A missing companion flag is a **usage** error, not a config one: `vault path inbox` without
`--extractor` exits 2, matching `index context` given neither `ENTITY` nor `--permalink`. It used
to exit 3, so one command answered two variants of the same mistake with two different codes.

### The change manifest

`ChangeManifest`, written to `<vault.root>/<layout.meta>/<layout.manifests>/<cycle_id>.json` and
copied to `manifest.latest_filename`. It is the value every post-cycle hook receives and the
document `run --once --json` prints.

```
ChangeManifest
  cycle_id     str          "20260805T101503Z-a3f1c2" -- sortable, collision-safe
  started_at   datetime     tz-aware UTC
  finished_at  datetime
  extractors   [ExtractorChanges]
  index        IndexOutcome | None      None when the index step did not run
  hooks        [HookOutcome]
  ok           bool         computed: no extractor error, no index errors, no hook error

ExtractorChanges  name, started_at, finished_at, item_count, changes: [FileChange], error: str|None
FileChange        path (vault-relative, POSIX), kind: added|updated|removed, record_ids: [str]
IndexOutcome      roots, indexed, skipped, pruned, errors, elapsed_seconds
HookOutcome       spec, error: str|None
```

Helpers: `paths(*, kind, extractor)` and `failures()`.

`record_ids` is empty except on files that hold many records — the two Teams extractors declare the
ids they merged in that pass (ADR 0020). Everything else is captured by `RecordingStorage`, so an
extractor cannot write into the vault without appearing here.

The manifest is written **twice** per cycle: once after the index step and before hooks fire, and
once after, with the hook outcomes filled in. A hook that takes the process down must not take the
record of what was extracted with it. Older manifests are pruned to `manifest.retain_cycles`,
oldest first.

### Hooks

```yaml
hooks:
  post_cycle:
    - "my_package.hooks:on_cycle"
```

- **Spec format** is `module.path:callable`. The colon is required: `a.b.c` cannot say whether `c`
  is a submodule or an attribute.
- **Signature** is `def hook(manifest: ChangeManifest) -> None`. One positional argument, no
  keywords, return value ignored.
- **Validated for shape at config parse; imported at workspace open.** Parsing never imports, so
  `config show` is not arbitrary code execution. `config validate` does resolve, which is what makes
  it a preflight.
- **Error policy.** A raising hook is caught, logged with its full traceback, recorded as
  `HookOutcome(spec, error)`, and persisted. The remaining hooks still run; the cycle still
  completes; `manifest.ok` is false, `run --once` exits 1, and `status` reports it. There is no
  timeout — a thread-based one cannot stop a blocked callable.

### The state store

```
StateStore (Protocol)
  get(namespace, key) -> dict     {} when absent; absence is not an error
  put(namespace, key, value)      replaces
  delete(namespace, key)          idempotent
  keys(namespace) -> [str]        sorted; [] for an unknown namespace
```

`JsonStateStore(root)` writes one file per namespace under
`<vault.root>/<layout.meta>/<layout.state>/`, rewritten atomically.
`InMemoryStateStore` is the shipped fake; both run the same conformance suite.

Three namespaces, and only three:

| Namespace | Key | Value |
|---|---|---|
| `extractor_state` | extractor name | delta tokens and watermarks |
| `cursors` | unit name | `last_run_at`, `last_success_at`, `consecutive_failures`, `last_error` |
| `cycles` | cycle id | one-line summary of a finished cycle |

`last_run_at` and `last_success_at` are separate: advancing the first on failure stops a broken
extractor hot-looping against a dead endpoint, and holding the second back keeps the staleness
visible.

### Protocols

The seam set. One implementation each today, each shipping an in-memory fake (ADR 0010).

| Protocol | Module | State | First implementation |
|---|---|---|---|
| `StorageBackend` | `m365_brain/storage/base.py` | **Present** | local filesystem, Azure Blob |
| `IndexBackend` | `m365_brain/index/backends/base.py` | **Present** | SQLite + FTS5, plus `InMemoryIndexBackend` |
| `EmbeddingProvider` | `m365_brain/index/vector/base.py` | **Present** | fastembed |
| `VectorStore` | `m365_brain/index/vector/base.py` | **Present** | sqlite-vec |
| `IntentStore` | `m365_brain/outbox/stores.py` | **Present** | `FilesystemIntentStore` + `InMemoryIntentStore` |
| `OutboxHandler` | `m365_brain/vault/dispatch.py` | **Present** | `EmailOutbox`, `TeamsPostOutbox`, `FileUpdateOutbox` |
| `StateStore` | `m365_brain/state.py` | **Present** | `JsonStateStore` under the meta directory, plus `InMemoryStateStore` |

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

That retry policy covers the **token** call as well as the data call, because the Authorization
header is built inside the retry branch. MSAL reaches the identity provider over `requests` rather
than `httpx`, so the auth adapter translates a transport fault into
`m365_brain.m365.errors.AuthTransportError` and `GraphClient` retries it under the same
`graph.max_retries` / `graph.backoff_base_ms` policy. Surviving that many attempts means the
identity provider is unreachable, so the exception that finally escapes is deliberately **not** a
`GraphApiError`: the per-item handlers that let an extractor survive one unreadable item must not
survive a dead IdP by skipping every remaining item and reporting a clean sync. A caller that means
"any failure of this call" catches `(GraphApiError, httpx.TransportError, AuthTransportError)`.

Every extractor takes `run(client, storage, state, config, ctx)` where `ctx` is an
`ExtractorContext(paths, converters, removal)`. One shape for all eight — `EXTRACTORS` no longer
carries a `needs_converters` flag, because there is no longer a second call shape to dispatch to.

**Present** for the outbox half:

- `m365_brain.m365.auth.profiles.AuthProfiles(profiles, timeout_seconds)` — `provider(name)`,
  `login(name)`, `status(name)`, `scopes(name)`. One MSAL app and one token cache per named
  profile; two profiles sharing a `token_cache_path` is refused at construction.
  `timeout_seconds` is `graph.timeout_seconds` and is **required** — MSAL passes no timeout of its
  own, so `requests` would otherwise wait forever on a black-holed connection to the identity
  provider. There is one such ceiling for M365 HTTP, not one per transport. The same required
  parameter is carried by `DeviceCodeAuth`, `AuthCodeAuth`, `make_cli_token_provider` and
  `make_web_token_provider`.
- `m365_brain.m365.files` — `resolve_site_id`, `resolve_drive_id`, `resolve_default_drive_id`,
  `list_children`, `get_file`, `item_etag`, `download_file_bytes`, `create_file`, `update_file`.
- `m365_brain.m365.outboxes.build_handlers(outboxes_config, upload_config, clients)` — one handler
  per configured outbox, each bound to the `GraphClient` of the auth profile its config names.
- `m365_brain.outbox.build_registry(outboxes_config, auth_profiles, handlers)` — handlers are
  **injected**, so no module imports both `outbox` and `m365` (ADR 0014).
- `m365_brain.outbox.runner.push(store, registry, router) -> PushCounts` and
  `reconcile(store, fetch, markers) -> list[ReconcileOutcome]`. The reconciliation Graph fetch is a
  `(mailbox, message_id, select) -> dict | None` callable supplied by the caller, and that `None` is
  an **obligation on the caller**, not merely a permitted return: a message Graph no longer holds
  must arrive as `None`, because it is the only signal that produces the `rejected` verdict. An
  adapter that raises instead ends the pass — and since a receipt is marked only *after* its fetch
  returns, that one receipt then ends every later pass at the same point, which is a stalled
  backlog rather than a flaky run. Only the missing message converts; every other Graph failure
  propagates, because `rejected` is terminal and an outage must never be filed as a human decision.

`ReconcileOutcome` carries `uuid`, `verdict` (`sent` | `amended` | `rejected` | `pending`),
`graph_message_id`, `conversation_id`, `sent_at`, `sent_body_html`, `original_body`. The sent HTML
and the original body travel **by value**, so no knowledge-base path crosses the boundary in either
direction; `post_reconcile` hooks do the projection.

**Present.** `m365_brain/workspace.py` — the facade that opens a config and returns a working
handle: `sync`, `sync_vectors`, `search`, `find`, `observations`, `context`, `recent`,
`recent_total`, `catalog`. It is the API the bundled skills and the CLI target, and every method is
one call to the module that owns the behaviour. No method takes a default argument: `full_rebuild`,
`mode`, `page`, `page_size`, `entity_type` and `max_depth` each change what the call does, and a
call site that does not say which it wants has not decided.

### Bundled skills

`skills/m365-brain-{knowledge,files,ops}` in agentskills.io format — package-prefixed names, and no
environment of their own (ADR 0022). They shell out to the installed console script and read their
config path from `M365_BRAIN_CONFIG`. Their contract to an adopter: every threshold and rule they
apply is traceable to a named config key, tabulated in
`skills/m365-brain-ops/references/config-keys.md` (ADR 0008).

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
   reproduces it; the index never holds the only copy of anything. *(Present — `index rebuild
   --yes` is the operation, and `--root` scopes the prune to the roots it walked.)*
6. **Re-running is safe.** Extraction, indexing, intent push, and reconciliation are idempotent:
   a second run over unchanged input produces no additional side effect. Intents carry a
   client-supplied idempotency key so a retried push does not duplicate a draft. *(Present —
   indexing included: the sync is checksum-driven, so a second pass over unchanged files skips
   them.)*

   Corollary, also **Present**: an intent claimed with no recorded outcome is **never
   auto-retried**. Repeating a send whose result is unknown duplicates mail, which is the failure
   an outbox exists to prevent. See ADR 0017, including the named non-atomicity of the claim.
7. **Upstream deletion propagates exactly once.** One canonical removal handler maps an upstream
   id to its written path, hard-deletes, and drops the state-map entry, so a repeated `@removed`
   does not re-404. *(Present for the six extractors with an upstream removal signal; the two
   Teams extractors have none, per the table above.)*
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
    fail-loud, scoped to consumer-supplied code so one consumer's bug cannot wedge extraction —
    and it still degrades the cycle's verdict, so nothing about the outcome claims success.
    *(Present.)*
14. **The manifest equals what the cycle wrote.** Every write reaches the vault through
    `RecordingStorage`, so a path on disk that is absent from the manifest is impossible rather
    than unlikely. Asserted for all eight extractors against a real backend. *(Present — ADR
    0020.)*
15. **Results go to stdout, logs go to stderr.** Every read verb offers `--json`, and its output
    parses without first being separated from log noise. *(Present.)*
13. **No consumer vocabulary appears anywhere in the repo.** Enforced by
    `scripts/check_publishable.py` over all tracked text, in pre-commit and CI. *(Present.)*

## Pydantic / schema source

Authoritative for field-level detail; this document summarises.

| Contract | Source |
|---|---|
| Config root and every section | `m365_brain/config/schema.py` — `Config` and its frozen, strict sub-models |
| Config loading, merge, env expansion, path resolution | `m365_brain/config/loader.py`; errors in `m365_brain/config/errors.py` |
| Storage seam | `m365_brain/storage/base.py` |
| Sync state, cursors, cycle history | `m365_brain/state.py` — `StateStore`, `JsonStateStore`, `InMemoryStateStore` |
| Change manifest and the recording seam | `m365_brain/manifest.py` — `ChangeManifest`, `RecordingStorage`, `ManifestStore` |
| Due computation and cursor bookkeeping | `m365_brain/schedule.py` |
| Hook resolution and dispatch | `m365_brain/hooks.py` |
| One cycle, and the loop around it | `m365_brain/cycle.py`; the index half in `m365_brain/index_step.py` |
| CLI verbs and exit codes | `m365_brain/cli.py` and `m365_brain/commands/` |
| Markdown frontmatter per entity type | `m365_brain/m365/frontmatter/` |
| Admin/worker database tables | `m365_brain/models.py` (SQLModel), migrated by `alembic/` |
| Knowledge model — `Entity`, `Observation`, `Relation`, `SearchPage`, `CatalogEntry`, `CatalogQuery` | `m365_brain/model.py` |
| Index, vector, and embedding seams | `m365_brain/index/backends/base.py`, `m365_brain/index/vector/base.py` |
| Intent envelope and per-outbox payload schemas | `m365_brain/vault/intent.py` and `m365_brain/vault/payloads.py` (ADR 0014) |
| Dispatch vocabulary — `GraphOp`, `DispatchResult`, `DispatchReceipt`, `OutboxHandler` | `m365_brain/vault/dispatch.py` |
| Authority state machine | `m365_brain/outbox/authority.py`; guards in `m365_brain/outbox/registry.py` |
| Reconciliation outcome | `m365_brain/outbox/reconcile.py` |

Structural contracts that are not Pydantic — allowed directories, import direction, module size,
test presence — live in `scripts/check_structure.py`, which is the executable statement of the
repository layout in the same way the models are of the data.
