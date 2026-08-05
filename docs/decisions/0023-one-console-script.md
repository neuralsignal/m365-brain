---
title: "ADR 0023: One console script, no short alias"
type: adr
permalink: adr-0023-one-console-script
tags:
  - adr
---

# ADR 0023 — One console script, no short alias

**Status:** Accepted (2026-08-05)

## Context

An earlier design sketch offered `mb` as an optional two-letter alias beside `m365-brain`, on the
reasoning that the full name is long and every command starts with it.

## Decision

`m365-brain` is the only entry point. `pyproject.toml` declares one console script and there is no
second name.

## Consequences

- **No PATH squatting.** `mb` is two characters in a namespace shared with every other tool the
  user has installed. Taking it costs them a name they may already want, and buys them one line of
  shell they can write themselves:

  ```bash
  alias mb='m365-brain'
  ```

- **One name to document.** Two entry points is two things in every example, two things in every
  skill, and two things that drift the first time one of them is renamed.
- **The verbosity is real and small.** In practice the length that matters is
  `--config "$M365_BRAIN_CONFIG"`, not the six characters saved on the program name, and that is
  addressed by the environment variable rather than by a shorter binary.

## Related

Superseded from the sketch, not from a shipped feature — no alias was ever released, so nothing
breaks.
