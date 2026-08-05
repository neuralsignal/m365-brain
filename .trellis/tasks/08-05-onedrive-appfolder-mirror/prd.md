# OneDrive AppFolder mirror and outbox intake

Deferred by ADR 0011. **Read that ADR before starting** — it records why this was cut, and the
two fixes below are the reason it is worth more as a document than as code today.

## What it was

~750 lines mirroring the local vault into a OneDrive AppFolder (`/Apps/<app-name>/`), so a phone
or a second machine could drop a file into a synced folder and have it picked up as outbox intake.
It is the single largest and highest-risk component in the source material, and it existed to
mirror a directory that is already a local folder.

## Why it is not built

Nothing needs it. The vault is a local directory and every consumer reads it directly. Rebuild
this only when there is a real second writer — a mobile client, or a machine that cannot mount
the vault.

## The scars — these are the point of this document

Both were production failures, and both have narrower-looking fixes that re-open the hole.

**1. Skip ALL `/Apps/<*>/` subtrees, not only this application's own.**
The mirror writes into `/Apps/<app-name>/`. The OneDrive extractor walks the drive. Unless the
extractor skips the *entire* `/Apps/` tree, it re-ingests what the mirror just wrote, the mirror
sees new vault content, and writes again. Skipping only this app's own folder is the tempting
narrower fix and it is wrong: another application's AppFolder is still noise the vault has no
reason to hold, and renaming the app silently re-opens the loop.

**2. Block the feedback loop at the source, not by filtering downstream.**
Filtering mirrored files out *after* enqueueing still enqueues them. The queue is the resource
under pressure, so a downstream filter leaves the producer running flat out. The block belongs in
the walk.

**The incident: 22 minutes of head-of-line blocking.** The feedback loop saturated the work queue
with mirror-generated churn. The queue was FIFO and single-headed, so every unrelated extraction —
email, calendar, Teams — sat behind it for 22 minutes. Nothing crashed and nothing logged an
error; the system stopped making progress on anything a user cared about while reporting itself
healthy. A retry policy or a larger queue would have made it longer, not shorter.

## If this is rebuilt

- Both fixes above from the first commit, with a test that plants a file under `/Apps/other-app/`
  and asserts the walk never yields it.
- A per-extractor bound on queue occupancy, so no single source can head-of-line-block the rest.
  The unbounded single queue is the incident's real root cause; the mirror only exposed it.
- The OneDrive storage backend's 4 MiB single-shot PUT cap returns with it (ADR 0011). Anything
  larger needs a chunked upload session.

## Non-goals

- Reviving the outbox intake path on its own. `file.update` is the surviving write path and it
  does not need a mirror.
