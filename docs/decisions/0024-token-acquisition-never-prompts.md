---
title: "ADR 0024: Token acquisition never prompts"
type: adr
permalink: adr-0024-token-acquisition-never-prompts
tags:
  - adr
---

# ADR 0024 — Token acquisition never prompts

**Status:** Accepted (2026-08-27)

## Context

`DeviceCodeAuth.get_token` was "try the cache, else run the device-code flow". That reads as a
convenience: a first-time user runs any command and is walked through login.

But `commands/_context.py` builds **one** token provider and hands the same callable to every
consumer, `m365-brain run` included. So the daemon held a method that could decide, at any
moment, to print a code and block until a human typed it somewhere.

On 2026-08-26 it did. The laptop slept, DNS stopped resolving
`login.microsoftonline.com`, one silent refresh failed, and the sync — 77 clean cycles that day —
printed a device code into a tmux pane nobody was attached to and blocked in
`acquire_token_by_device_flow`. The code expired fifteen minutes later. The process was still
alive, still blocked, twelve hours on. Nothing in the logs said "stuck"; the last line was an
ordinary `AADSTS70016: Authorization is pending. Continue polling.`

A daemon cannot answer a prompt. Being asked one is indistinguishable from being dead, except
that a dead process gets restarted.

## Decision

`get_token` reads the cache and raises `AuthRequiredError` when it cannot produce a token. It
never starts an interactive flow.

`login()` is unchanged and remains the only interactive entry point. It is what `auth login`
already called — the two paths were always separate, and no caller needed the fallback.

## Consequences

- **A network blip is now self-healing.** The extractor fails, `cycle.py` records it, the loop
  continues, and the next cycle succeeds on its own once DNS returns. Previously the first blip
  after a sleep was terminal.
- **`AuthRequiredError` is deliberately not a `GraphApiError` and has no `transient` attribute.**
  The first keeps the twelve per-item extractor handlers from swallowing "we have no credentials"
  and recording a successful sync with missing data; the second stops the outbox putting an intent
  back for a retry that cannot mint a refresh token. Same two reasons as `AuthTransportError`,
  opposite answer on transience.
- **First-run setup is now explicit.** A fresh install running `m365-brain run` gets an error
  naming its `token_cache_path` and the `auth login` command, instead of a prompt. The
  quickstart already documented `auth login` as its own step, so no guide changed.
- **Transport faults are untouched.** A DNS failure *during* a refresh still raises
  `AuthTransportError`, is still retried by `GraphClient`, and still recovers within the cycle.
  Only the "silent refresh returned nothing" branch changed.

## Related

`AuthTransportError` (ADR-less, documented in `m365/errors.py`) covers the retryable half of the
same failure surface.
