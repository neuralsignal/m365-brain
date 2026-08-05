---
title: "ADR 0016: There is no unconditional file-write function"
type: adr
permalink: adr-0016-no-unconditional-write
tags:
  - adr
---

# ADR 0016 — There is no unconditional file-write function

**Status:** Accepted (2026-08-05)

## Context

The SharePoint writer absorbed into `m365/files.py` exposed one write function:

```python
def put_file(token_provider, drive_id, item_path, content, content_type, if_match: str | None) -> str
```

`if_match=None` meant "overwrite unconditionally". Its one production caller computed the argument
as `None if meta.get("missing") else meta.get("etag")` — so a staging sidecar that had lost its
`etag` key produced `None`, and a file a human had hand-edited was replaced without a word.

That is the only silent-data-loss path in everything this package absorbed, and it is worth being
precise about where it lived: **not in the Graph call, in the nullable**. The call did exactly what
it was told. A parameter whose `None` means "skip the safety check" gets passed `None` eventually,
by a caller that did not know that is what it was saying.

## Decision

Remove the nullable from the public surface. `m365/files.py` exposes two disjoint functions and no
third:

```python
def create_file(client, upload, drive_id, item_path, content, content_type) -> str
def update_file(client, upload, drive_id, item_path, content, content_type, etag: str) -> str
```

- `create_file` reads before writing and raises `GraphConflictError` if the item exists.
- `update_file` requires a non-empty `etag`; an empty one raises `ETagRequired` **before any
  request is made**. A stale one raises `GraphConflictError` (HTTP 412) and nothing is written.
- The shared `_write` that still takes `if_match: str | None` is private, so the nullable cannot be
  reached from outside the module.

`FileUpdatePayload.etag: str | None` then routes structurally: `None` to create, a string to
update. There is no boolean flag and no third path, so an intent cannot express an unconditional
overwrite.

`tests/unit/m365/test_files.py::test_no_public_function_takes_a_nullable_if_match` reads the
module's own signatures. The guarantee is structural; a test that only checked behaviour would not
notice a new function reintroducing the parameter.

## Consequences

- **The 412 test asserts on the call log, not on the exception.** "It raised" is not the property;
  "it did not write" is, and only the recorded requests can say so.
- **`create_file`'s read-before-write is not atomic.** Graph's simple `/content` PUT is
  create-or-replace and `conflictBehavior: fail` exists only on the upload-session path. The race
  that actually costs data — two people editing the same document over hours — is covered by the
  eTag; the create race is a millisecond window between two writers creating the same new file.
  Marked in the source.
- **Above the simple-upload ceiling the guarantee weakens and says so.** An upload session takes no
  `If-Match` and its chunk PUTs cannot carry one, so `update_file` re-reads the eTag immediately
  before opening the session and raises if it moved. That is weaker than a conditional write and
  it is documented in `_write_session` rather than hidden. It still converts the realistic case —
  someone edited the file yesterday — from a silent clobber into a raised conflict.
- **The conflict surfaces as a lifecycle outcome, not a crash.** A `file.update` intent whose eTag
  is stale is archived to the rejected tree with `reason: etag_conflict`, and the push pass
  continues. Direct CLI callers get the exception.
