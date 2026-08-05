---
title: "ADR 0018: Intents are markdown with a typed, discriminated payload"
type: adr
permalink: adr-0018-intents-are-typed-markdown
tags:
  - adr
---

# ADR 0018 — Intents are markdown with a typed, discriminated payload

**Status:** Accepted (2026-08-05)

## Context

`CONTRACTS.md` previously described intents as "JSON files". The schema this package absorbed
chose markdown-with-frontmatter instead, and that choice is right for two reasons the port must
keep: an agent authoring an email body writes prose, and prose escaped into a JSON string is
unreviewable; and a markdown file is diffable and indexable by the same machinery that reads every
other file in the vault.

The same schema also had `payload: dict[str, Any]`, with a per-outbox Pydantic model fetched from a
registry and then **discarded** — so `extra="forbid"` and `strict=True` were set on models that
never ran. No production payload was ever validated.

## Decision

The frontmatter is the envelope; the markdown body is the payload's `body` field; the payload is a
**discriminated union** on `kind`.

```
uuid · schema_version · created_at · created_by · payload
```

Deliberate differences from the schema this ports:

| Their field | Here | Why |
|---|---|---|
| `outbox: str` | dropped | duplicates `payload.kind`; their intake worker needed an explicit gate purely to police the duplication |
| `integration: str` | dropped | a multi-source registry is a non-goal (ADR 0012) |
| `payload: dict[str, Any]` | discriminated union | a `dict` is why validation could be skipped; a union makes it structural |
| `body: str = ""` on the envelope | a field on each payload | their loader did `metadata["body"] = post.content`, silently clobbering a frontmatter `body:` key |
| — | `schema_version: int` | the archive is a ledger, and a ledger row with no version cannot be read twice |

Three rules follow and each is a hard parse error:

1. a frontmatter `body:` key, or a `payload.body:` key — the body comes from the markdown and
   nowhere else;
2. an envelope `uuid` that differs from the filename stem — the source never cross-checked these,
   so its database row, its blob path and its processed archive used one value while its rejected
   archive used the other;
3. any unknown key, anywhere, under `extra="forbid"`.

`X | None` payload fields carry **no default**: an author who omits `cc:` gets an error naming the
key rather than a silent empty list.

## Consequences

- **`email-validator` is a dependency**, for `EmailStr`. Worth it: the pipeline this replaces
  auto-split a bare `to: a@x.com; b@y.com` string into a list with a logged warning, which is the
  helpful coercion that lets a malformed recipient reach Graph.
- **`include_signature` is the polarity flip of `skip_signature`.** Migration is
  `include_signature = not skip_signature`. It has no default, so every intent states it — a
  silently inverted boolean is the most plausible port bug in this area and the authoring template
  must flag it.
- **`in_reply_to` is a Graph message id, not a permalink.** Permalink resolution used a filesystem
  glob at dispatch time, which makes the same file produce different Graph requests depending on
  what is on disk — not idempotent, and it would have made the request-parity gate untestable.
  Resolution moves to authoring time. The legacy MAPI EntryID guard travels with the type instead,
  as a field validator, so it fires at parse for every intent rather than only for resolved ones.
- **`dump_intent` serialises in Python mode, not JSON mode**, so `created_at` stays a datetime that
  YAML writes as a native timestamp. Dumping an ISO string would round-trip through YAML as a
  string and fail the envelope's own `strict=True` — an archive that cannot re-read itself.
- **Attachments are paths, not inline base64.** A base64 blob in YAML is unreadable and undiffable,
  and Graph's bulk cap means the model could not express what its own executor needs anyway.
