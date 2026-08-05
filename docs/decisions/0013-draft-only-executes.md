---
title: "ADR 0013: A draft_only outbox executes"
type: adr
permalink: adr-0013-draft-only-executes
tags:
  - adr
---

# ADR 0013 — A `draft_only` outbox executes

**Status:** Accepted (2026-08-05)

> This ADR records a **correction to ported logic**. That is the class of change a silent port
> loses: the new code disagrees with the old code on purpose, and without this document the next
> person to compare them will read the difference as a porting mistake and "fix" it back.

## Context

The tier router absorbed into this package maps `(tier, status)` onto one of four actions. Its
table contained:

```
(DRAFT_ONLY, "pending") -> "archive"
```

All three of its email outboxes were `draft_only`. Under its own router, therefore, every email
intent would have been filed into the processed archive without a draft ever being created in
Outlook.

Three other artifacts in the same codebase say the opposite:

- the router module's own docstring — "the agent drafts; the platform never sends";
- its design document, which describes the drafting call as the execution step;
- its ADR on the tier model, which states that the SaaS API call *is* a drafting operation and is
  therefore performed.

The code was the outlier. It never mattered in production because that codebase's registry was
never populated, so no intent reached the router at all.

## Decision

The router in this package maps `(draft_only, pending)` to `execute`.

`draft_only` means "the only Graph operations this outbox may perform are drafting operations",
not "this outbox performs no operations". Two static guards enforce that meaning at process start:
the handler's `declared_ops` must be a subset of `{create_draft, update_draft, attach}`, and the
Entra app the outbox names must not hold a scope in `outboxes.forbidden_send_scopes`.

The corrected row carries a comment in `m365_brain/outbox/tiers.py`, and
`tests/unit/outbox/test_tiers.py::test_draft_only_pending_executes_and_this_is_deliberate` pins it
with the reason.

## Consequences

- An email intent produces a draft. That is the whole point of the subsystem, so any table that
  archives it instead is a bug regardless of which codebase it came from.
- `draft_only` now earns its keep through the two guards rather than through the router. That is a
  stronger guarantee: a router entry is a runtime behaviour a caller can route around, while a
  scope that was never granted cannot be used by any code path at all.
- A reader diffing this package against its source will find exactly one changed row in the
  transition table. It is this one, and it is intentional.
