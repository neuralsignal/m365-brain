---
title: "ADR 0020: A FileChange carries the record ids merged into it"
type: adr
permalink: adr-0020-manifest-carries-record-ids
tags:
  - adr
---

# ADR 0020 — A `FileChange` carries the record ids merged into it

**Status:** Accepted (2026-08-05)

## Context

The change manifest exists to replace two things at once: consumers re-scanning the vault to find
out what changed, and consumers keeping their own watermark file to remember what they had already
seen. The first falls out of any list of paths. The second does not.

For an extractor that writes one file per upstream item — email, calendar, contacts, directory,
OneDrive, SharePoint — the path *is* the identity. `inbox/emails/2026/…/index.md` appearing in the
manifest as `added` is a complete statement: one new message, and here it is.

The two Teams extractors do not work that way. A conversation is one directory holding one merge
store and one rendered markdown file, both rewritten whole on every pass. A path-level manifest
says `inbox/teams-chats/alice-bob_a1b2c3/messages.md` changed, and a downstream trigger that wants
"tell me about new messages" must then re-read the file, re-match every message in it against
something it remembers, and decide which are new. That something it remembers is a watermark file
by another name — the exact artefact the manifest was supposed to delete.

This was not settled by the parent design. Left alone, the promise "the manifest is the watermark"
would have held for six extractors and quietly failed for two.

## Decision

`FileChange` carries `record_ids: list[str]` — the upstream record ids merged into that file in
this pass.

It is populated by one explicit call, `recorder.note_records(path, ids)`, made only by the two
extractors that already compute that set. `merge_messages` was changed to return the merged ids
instead of a bare `changed` flag; the flag is the list's truthiness, so nothing lost information.
Everywhere else the list is `[]`.

This is the one thing in the manifest a producer must volunteer. Every other field falls out of
`RecordingStorage` wrapping the storage backend, where an extractor cannot write without being
recorded.

## Consequences

- **A merge-store consumer needs no state of its own.** `record_ids` answers "which records are new
  in this file" directly, which is what a trigger actually asks.
- **It is only as honest as `merge_messages`.** A property test asserts the returned ids are
  exactly the records whose etag changed or which were absent — if that drifts, `record_ids`
  becomes a plausible lie rather than a visible failure.
- **`note_records` can be forgotten.** Unlike a write, it is not captured structurally. The
  mitigation is a test asserting that the two merge-store extractors declare ids and that the other
  six declare none — so adding a ninth extractor of either shape forces a decision.
- **The ids are attached to the rendered conversation file**, not the merge store. That is the file
  a consumer opens; the store is documented as a sync artefact that indexers skip.
- **YAGNI was checked.** This ships because a concrete consumer exists today, not because merge
  stores might one day need it.
