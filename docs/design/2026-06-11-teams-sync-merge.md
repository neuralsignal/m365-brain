# Teams Sync v2 — Merge-Based Incremental Sync for Chats and Channels

Status: approved design, 2026-06-11. Implements whole-conversation retention,
standardized markdown rendering, and incremental watermark sync for
`teams_chats` and `teams_channels`.

## Problems with v1 (current code)

1. `teams_chats` sends `$filter=lastModifiedDateTime gt X` with
   `$orderby=createdDateTime desc`. Graph **silently ignores** the filter
   unless `$orderby` targets the same property
   ([chat-list-messages](https://learn.microsoft.com/en-us/graph/api/chat-list-messages?view=graph-rest-1.0)).
   Net effect: every active chat re-fetches up to 20 pages every poll.
2. `messages.md` is rebuilt purely from the current fetch (no merge). Once a
   chat exceeds `max_messages_per_chat` (1000), older history silently rolls
   off and is permanently lost from the vault.
3. `teams_channels` uses `/messages/delta`, which is (a) no longer documented,
   (b) known-flaky (intermittent 400s), (c) loses its deltaLink whenever
   `max_pages` is hit before the final page (deltaLink only appears on the
   last page), and (d) **never returns replies**, so threads are mostly empty.
4. `get_paginated` warns `max_pages_reached` even when the final allowed page
   completed the data (false positive).

## Verified Graph API constraints (delegated permissions)

- No delta API is available: `chats/getAllMessages/delta` and
  `getAllMessages` are application-permission-only; per-chat delta does not
  exist. Channel `/messages/delta` is undocumented/flaky — do not use.
- Chat messages: `$top` ≤ 50; `$orderby` only `lastModifiedDateTime desc`
  (default) or `createdDateTime desc`; `$filter` works **only** when paired
  with `$orderby` on the same property; `lastModifiedDateTime` supports `gt`.
- Channel messages (non-delta list, `ChannelMessage.Read.All` delegated):
  root messages only, `$top` ≤ 50, **no** `$filter`/`$orderby`; response is
  sorted by **last modified of the entire reply chain, descending** — a new
  reply bubbles its root to the front. `$expand=replies` returns up to ~200
  replies inline plus `replies@odata.nextLink` for more.
- Replies endpoint: `$top` ≤ 50, pagination via `@odata.nextLink`, ordering
  not contractual — sort client-side.
- `lastModifiedDateTime` moves on create, edit, soft-delete, and reactions.
  `lastEditedDateTime != null` → edited. `deletedDateTime != null` → soft
  deleted (stub remains in listings). `etag` is the version number.
- `messageType`: without the `Prefer: include-unknown-enum-members` header,
  system events surface as `unknownFutureValue`. Sync keeps **only**
  `messageType == "message"`.
- Throttling: 20 rps per app/tenant on message GETs, 1 rps per individual
  chat/channel. Sequential HTTP is comfortably inside limits.

## Architecture

```
fetch (Graph) ──► merge into per-conversation JSONL store ──► render messages.md
                          (source of truth)                     (derived artifact)
```

Two new shared modules (DRY between chats and channels):

### `m365_extract/extractors/_message_store.py`

Per-conversation message store, one JSON object per line at
`<conv_dir>/messages.jsonl`. Indexers in the consuming workspace scan `*.md`
only, so the sidecar is invisible to search/embedding.

```python
@dataclass(frozen=True)
class StoredMessage:
    id: str
    parent_id: str | None      # None for chat messages and channel roots
    sender: str
    created: str               # ISO 8601 from Graph createdDateTime
    last_modified: str         # Graph lastModifiedDateTime
    etag: str
    edited: bool               # lastEditedDateTime != null
    deleted: bool              # deletedDateTime != null
    content: str               # rendered markdown (html→md already applied)
    attachments: list[dict]    # serialized AttachmentRef dicts
    subject: str | None        # top-level message subject; None for replies
```

The module also exports `sort_key(msg) -> tuple[str, str]` — the canonical
`(created, id)` ordering used by `save_store`, the renderer, and both
extractors' last-message-time derivation (one definition, four consumers).

API (no default arguments anywhere):

- `load_store(storage: StorageBackend, path: str) -> dict[str, StoredMessage]`
  — `{}` when the file does not exist; raises `MessageStoreError` on a
  corrupt line (fail fast — the caller may delete the store to force a
  backfill, but the code never silently skips).
- `save_store(storage: StorageBackend, path: str, store: dict[str, StoredMessage]) -> None`
  — writes lines sorted by `(created, id)` for deterministic output.
- `merge_messages(store: dict[str, StoredMessage], fetched: list[StoredMessage]) -> tuple[dict[str, StoredMessage], bool]`
  — upsert by `id`; replace only when `etag` differs; returns
  `(new_store, changed)`. Pure function; property-tested (idempotent:
  merging the same batch twice == once; merge never drops existing ids).

### `m365_extract/extractors/_message_renderer.py`

Renders a store into the standardized markdown body. Pure functions,
property-tested for determinism (same store → identical output).

Standard format — chats:

```markdown
## 2026-06-11

### 09:42 — Matthias Christenson

message content

**Attachments:** [report.pdf](attachments/<msg-id>/report.pdf) · [report.pdf (text)](attachments_converted/<msg-id>/report.pdf.md)

### 10:20 — Samuel Scholl *(edited)*

message content
```

Standard format — channels (threaded):

```markdown
## 2026-06-11

### 09:42 — Sender — Subject-or-first-line

root message content

#### ↳ 10:01 — Replier

reply content

#### ↳ 2026-06-12 08:15 — Replier

(reply header includes the date when it differs from the thread's day heading)
```

Rules:

- `## YYYY-MM-DD` day headings (chats: message day; channels: root-message
  day, the whole thread renders under its root's day).
- `### HH:MM — Sender` message headers, em dash separator (replaces the v1
  `### YYYY-MM-DD HH:MM -- Sender` form). All times UTC, as delivered by
  Graph.
- `*(edited)*` suffix when `edited`. Deleted messages render as
  `### HH:MM — Sender *(deleted)*` with the body `*Message deleted.*`
  (content from Graph is empty on tombstones).
- Channel thread title: root `subject` if set, else first non-empty content
  line truncated to 60 chars, else `Thread`.
- Messages sorted by `(created, id)`. Replies render under their root sorted
  the same way; replies whose `parent_id` has no stored root are rendered as
  top-level with a `*(orphaned reply)*` marker rather than dropped (fail
  visible, not silent).
- The file keeps the existing skeleton: frontmatter, `# Title`,
  `## Observations`, optional `## Relations`, `---`, `## Messages`, then the
  day-grouped timeline.

Frontmatter changes (both types): add `message_count: int` and
`history_complete: bool`. Remove `message_limit_reached`.

`history_complete` lives in **both** the extractor state
(`state["history_complete"][key]`) and the rendered frontmatter. It is derived
from the truncation signal of the backfill fetch (`not truncated` — a
remaining nextLink at the page cap, never a length heuristic) and is set
explicitly on every backfill. When the state key is missing, rendering
defaults to **false** (pessimistic: unknown completeness is incompleteness).

### Watermark state (in the extractor state dict, NOT frontmatter)

- `teams_chats` state: `{"watermarks": {chat_id: iso_last_modified}, "failed_attachments": {...}, "last_sync": ...}`
- `teams_channels` state: `{"watermarks": {f"{team_id}:{channel_id}": iso}, "failed_attachments": {...}, "last_sync": ...}`
- The old `delta_{team}_{channel}` keys and the chats `last_sync`-as-filter
  behavior are dropped (no backwards compatibility, per constitution).
  Stale keys in existing state files are ignored and overwritten on save.
- New watermark per conversation = max `lastModifiedDateTime` (chats) / max
  chain-modified (channels) observed in the fetch. Never `now()` — avoids
  clock-skew gaps. Unchanged when the fetch returns nothing.

## Extractor flows

### teams_chats

1. `GET /me/chats?$expand=members&$top=50` (unchanged).
2. Per chat:
   - Watermark present → incremental fetch:
     `GET /me/chats/{id}/messages?$top=50&$orderby=lastModifiedDateTime desc&$filter=lastModifiedDateTime gt {watermark}`,
     paged with the **global** `graph.max_pages` as the safety bound (the
     `$filter` already bounds the window — the per-chat backfill cap must NOT
     apply here, or older changed-but-unfetched messages would fall behind an
     advanced watermark forever). If even the global bound truncates the
     window (`get_pages` reports a remaining nextLink), the fetched messages
     are still merged but the run logs `teams_chats.incremental_truncated`
     at error level and does **not** advance the watermark — loud refusal
     beats silent loss; the next cycle retries the window.
   - No watermark (first run / store missing) → **backfill**: same endpoint,
     no filter, default ordering, page budget
     `max(1, ceil(max_messages_per_chat / 50))`;
     `history_complete = not truncated`.
   - If the store file is missing but a watermark exists (manual deletion),
     drop the watermark and backfill — the store is the source of truth.
3. Keep only `messageType == "message"`. Convert each kept message to
   `StoredMessage` via the shared `_teams_ingest.to_stored_message`
   (attachment-reuse rule below; `extract_content` with hosted-content
   rewrite unchanged).
4. `merge_messages`; on `changed`: `save_store`, render, write
   `messages.md`. On unchanged: write nothing — **except** that after any
   fetch that establishes a watermark, the store file is always saved (even
   empty) so store-file existence matches the watermark. A conversation whose
   fetched messages are all filtered out (e.g. a meeting chat containing only
   call events) therefore gets an empty `messages.jsonl` and **no**
   `messages.md`, and later cycles run incrementally instead of re-backfilling
   forever.
5. Update watermark (after a successful merge; see truncation rule above);
   save state once per run (as today).

Per-conversation isolation: the fetch, store load, and media-download phases
are each wrapped per chat/channel. `GraphApiError` (warning),
`httpx.TransportError` (error — e.g. escaping the hostedContents listing or
replies pagination), and `MessageStoreError` (error, with the store path and
remediation hint) skip that conversation **without advancing its watermark**
and the sync cycle continues with the others.

The v1 `loads_markdown`-based skip check is removed — the watermark plus
`changed` flag replaces it.

### teams_channels

1. `GET /me/joinedTeams` → `GET /teams/{id}/channels` (unchanged).
2. Per channel: `GET /teams/{tid}/channels/{cid}/messages?$top=50&$expand=replies`,
   pages in chain-modified-descending order. For each root:
   - If `replies@odata.nextLink` present, follow it (plus `/replies`
     pagination) to fetch the full reply set.
   - chain_modified = max(`lastModifiedDateTime` of root and all replies).
   - **Early stop**: watermark present and chain_modified ≤ watermark →
     stop paging entirely (everything after is older by the server sort).
   - No watermark → backfill until exhausted or `max_messages_per_channel`
     total stored messages; cap hit → `history_complete = false`.
3. Roots and replies (`messageType == "message"` only) become
   `StoredMessage`s (`parent_id` set on replies). Merge → render → write,
   same as chats (including the always-save-store rule and per-channel
   error isolation).
4. Output path (unchanged scheme): `teams-channels/<team-slug>/<channel-slug>-<hash6>/messages.md`
   — note this becomes a **folder** per channel (was a flat file) so
   attachments and the store sit beside it.
5. Attachments and inline images are now downloaded for channels too.
   Hosted-content base path differs:
   `/teams/{tid}/channels/{cid}/messages/{mid}` (and
   `.../messages/{mid}/replies/{rid}` for replies) vs `/chats/{cid}/messages/{mid}`.

### Config schema changes

`TeamsChannelsExtractorConfig` gains the same knobs as chats (all required,
no defaults):

```python
enabled: bool
poll_interval_minutes: int
max_messages_per_channel: int
download_attachments: bool
download_inline_images: bool
max_attachment_size_mb: int
attachment_convert_extensions: list[str]
```

`sync.py` dispatch: `teams_channels` flips to `needs_converters=True`.

### Attachment helper generalization

`_teams_attachment_helpers.py` is at the 300-line file cap. Split:

- `_teams_attachment_helpers.py` keeps reference-attachment download +
  conversion (`download_message_attachments` — already message-location
  agnostic; takes a `conv_dir` path prefix).
- New `_teams_hosted_content.py` holds `download_inline_images`, now taking
  an explicit `message_api_base: str` (e.g. `chats/{chat_id}/messages/{msg_id}`
  or `teams/{tid}/channels/{cid}/messages/{mid}/replies/{rid}`) instead of
  hard-coding the `/chats/` route.
- Both accept a narrow `AttachmentSettings` Protocol (the four attachment
  fields: `download_attachments`, `download_inline_images`,
  `max_attachment_size_mb`, `attachment_convert_extensions`) so chat and
  channel configs both satisfy it.

### Shared ingest module: `_teams_ingest.py`

The Graph-payload→StoredMessage conversion lives once, in
`m365_extract/extractors/_teams_ingest.py`, used by both extractors:

- `GRAPH_PAGE_SIZE = 50` — the documented Graph `$top` maximum for Teams
  message endpoints (an API protocol limit, not a config value); drives the
  `$top` strings and backfill page math in both extractors.
- `is_etag_fresh(existing, msg)` — the one etag-freshness check used by both
  extractors (`merge_messages` keeps its own internal check as the
  correctness backstop).
- `to_stored_message(..., prior: StoredMessage | None)` — converts a message
  and downloads its media, with the **attachment-reuse rule**: when `prior`
  exists, neither side is a tombstone, the message body was never edited
  (`lastEditedDateTime` null — the prior content, including its rewritten
  inline-image links, is still valid), and the payload's downloadable
  attachment name set equals the prior refs' name set, the prior content and
  attachment refs are reused and **no downloads run**. This stops
  reaction-only etag bumps from re-downloading media — and, critically, stops
  a now-failing download (transient, or source-deleted on the permanent
  skip-list) from replacing the stored message with fewer attachment refs
  (permanent link loss). Downloads run only when the message is new, the
  body was edited, or the attachment name set changed.
  `downloadable_attachment_names` (in `_teams_attachment_helpers`) mirrors
  the downloader's filter exactly; a test pins the equivalence.

## graph_client fixes

1. New truncation-aware pagination API:
   `get_pages(path, params, max_pages) -> tuple[list[dict], bool]` — returns
   `(items, truncated)` where `truncated` means an `@odata.nextLink` remained
   unfetched at the page cap (with a `max_pages_reached` warning including
   the sanitized remaining URL). `get_paginated` stays as a thin generator
   wrapper over `get_pages` for callers that just iterate items.
2. `get_delta`: when the page cap is hit mid-round, return the pending
   `@odata.nextLink` as the resume link instead of `None`/a stale value.
   nextLink is a valid opaque resume URL; callers persist it exactly like a
   deltaLink, so the next cycle continues where this one stopped instead of
   re-fetching from the round's start forever. (Benefits email/onedrive/
   sharepoint/contacts; teams_channels stops using get_delta entirely.)
3. Resume-link consequence for delta callers: `email` and `contacts` derive
   their per-cycle delta page budget from `max_items_per_sync`
   (`ceil(max_items / page size)`, using the `$top` each sends — 50 for
   email; the server default of 10 for contacts, whose delta endpoint rejects
   `$top`) and process **everything** fetched. The old post-hoc
   `messages[:max_items]` slice is gone — with a persisted resume link it
   silently skipped the unprocessed tail forever.

## Testing (TDD, pytest + hypothesis)

New/changed tests, written before implementation:

- `test_message_store.py`: round-trip serialization (hypothesis), merge
  idempotency (hypothesis: merge(merge(s, f), f) == merge(s, f)), merge
  never removes ids, etag-change replaces content, corrupt line raises.
- `test_message_renderer.py`: determinism (hypothesis), day grouping,
  edited/deleted markers, cross-day reply headers, thread title fallbacks,
  orphaned-reply rendering, attachment link line.
- `test_teams_chats.py` (rewrite): incremental request shape asserts
  **$orderby and $filter target lastModifiedDateTime together**; backfill
  pagination to exhaustion; merge preserves messages absent from the
  current fetch (the v1 data-loss regression test); no write when nothing
  changed; cap → `history_complete: false`; non-`message` types skipped;
  store+render round trip through the real storage backend.
- `test_teams_channels.py` (rewrite): `$expand=replies` request shape;
  `replies@odata.nextLink` following; early stop at watermark (assert later
  pages NOT requested); replies present in output under their root;
  attachments/inline images for channel messages; folder layout.
- `test_graph_client.py`: warning fires only on real truncation; get_delta
  returns pending nextLink at cap.

Done = full suite green, `pixi run lint` + `pixi run format-check` clean,
coverage ≥ 80%, no file > 300 lines.

## Consuming-workspace rollout (Brain repo, separate commit)

1. `m365-data-sync/config/m365-extract.yaml`: add `ChannelMessage.Read.All`
   scope (admin consent already granted); enable `teams_channels` with the
   new fields; raise `max_messages_per_chat` to 10000 (backfill headroom).
2. `.gitignore`: add `knowledge/teams-channels/`.
3. `rules/knowledge-folder-structure.md`: add the teams-channels row.
4. First run after deploy: every chat backfills (no watermarks yet) and all
   `messages.md` files are rewritten in the new format → one-time full FTS
   + vector re-embed. Subsequent cycles touch only changed conversations,
   which is the steady state the embedding pipeline expects.
