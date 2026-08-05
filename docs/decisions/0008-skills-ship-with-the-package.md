---
title: "ADR 0008: Agent skills ship with the package"
type: adr
permalink: adr-0008-skills-ship-with-the-package
tags:
  - adr
---

# ADR 0008 — Agent skills ship with the package

**Status:** Accepted (2026-08-05)

## Context

Roughly 3000 lines of agent-facing scripts — search, graph context, recent activity, note
validation, file catalog and extraction, contact resolution, inbox triage — lived in the consuming
workspace as three skill trees. They are how an agent actually drives a knowledge layer; the
library on its own is a set of functions and a database.

If they stay with the consumer, an adopter gets a library and no way to operate it, and the first
thing every adopter writes is a worse version of the same scripts. If they ship here, they must
stop carrying one operator's policy — and some of them are nothing but policy.

## Decision

`skills/{knowledge,files,ops}` ship in this repository in agentskills.io format, as thin wrappers
over the CLI and the `workspace.py` facade. The manifest schema is verified against agentskills.io
at implementation time rather than inferred from an example.

The rule that makes this safe: **if a heuristic cannot be expressed as config, it does not ship.**
Relationship-tier thresholds and inbox-triage rules are one operator's policy, and a package
carrying them is exactly the assumption this consolidation exists to remove. Anything that fails
that test stays with the consumer, and the skill exposes the seam instead of hardcoding a guess.

## Consequences

- Every threshold and rule in the shipped skills is traceable to a config key. That is the
  acceptance criterion, not an aspiration.
- The skills are wrappers, not a second implementation. Behaviour lives in the CLI and the facade,
  so a skill cannot drift from the library it drives.
- `scripts/check_no_workspace.py` runs over `skills/` like everything else, which is what catches
  a consumer assumption smuggled in as a default.
- The `ops` skill is the risky one and may end up smaller than its predecessor. A skill that ships
  less is the correct outcome when the remainder was policy.
- Publishing the skills to any registry is a separate, later decision. They ship in the repository;
  distribution is not part of this.
- Delivered by the runtime stage (S2).
