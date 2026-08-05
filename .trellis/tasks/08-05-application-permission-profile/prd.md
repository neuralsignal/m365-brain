# Application-permission auth profile for Teams delta sync

The workspace owner has offered to create a new Entra app registration — provisioned
programmatically, approved separately by them. This task is what that would be *for*. It is not
required by the consolidation; it removes two limits the consolidation cannot remove on its own.

## Why a fourth app, not one app

The obvious reading of "an app to handle everything" is consolidation, and consolidation is the
wrong move. Today's three profiles are a least-privilege split, and the split is load-bearing:

| Profile | Holds | Deliberately does **not** hold |
|---|---|---|
| `extractor` | `Mail.ReadWrite*`, `Calendars.Read`, `Chat.Read`, `ChannelMessage.Read.All`, `Files.Read.All`, `Sites.Read.All`, `Contacts.Read` | `Mail.Send` — which is why email outboxes can only draft |
| `filewriter` | `Files.ReadWrite.All`, `Sites.ReadWrite.All` | anything touching mail |
| `teamsbot` | `ChannelMessage.Send` | anything touching mail or files |

`outboxes.forbidden_send_scopes` turns that separation into a startup assertion: a `draft_only`
outbox whose profile carries `Mail.Send` **fails to start**. Merging the three would delete that
guarantee and make "an agent sent mail" a one-bug-away outcome instead of a
two-independent-changes-away one.

So: a **fourth**, narrow, read-only profile. The config already supports N named profiles, so
adding one is a config change with no code change.

## What it unlocks

**1. Delta sync for Teams — the only extraction gap left.**
`chats/getAllMessages` and its delta endpoint are **application-permission-only**; per-chat delta
does not exist at all. Two consequences, both recorded as verified constraints in
`docs/design/2026-06-11-teams-sync-merge.md`:

- **Every active chat re-fetches up to 20 pages on every poll.** Graph silently ignores
  `$filter=lastModifiedDateTime gt X` unless `$orderby` targets the same property, so the
  watermark cannot be pushed server-side. This is the single largest source of wasted Graph calls
  in the sync.
- **Neither Teams extractor can detect an upstream deletion.** `CONTRACTS.md`'s removal-coverage
  table lists six extractors as covered and `teams_chats` / `teams_channels` as **not**, for
  exactly this reason. A message deleted in Teams stays in the vault permanently.

**2. Unattended operation.** The delegated flows hold a refresh token in a cache that eventually
expires and needs an interactive re-auth. A client-credentials profile does not, which is a
precondition for the deferred deployed service (ADR 0010) as well as for a daemon that survives
a long absence.

## What it costs — decide these before provisioning

- **Application permissions are tenant-wide by default.** `Chat.Read.All` as an application
  permission reads *every chat in the tenant*, not the owner's. For a clinical organisation that
  is a materially different privilege from what is held today, and it is the reason this task
  exists as a decision rather than a step.
- **Scope it down.** Exchange application access policies (`New-ApplicationAccessPolicy`) restrict
  an app's mailbox reach to a named group; Teams has an equivalent for resource-specific consent.
  The app should be scoped to a single-member group from the first grant, not scoped later.
- **`getAllMessages` is a Teams *protected API*.** **Verify before committing to this** — the
  approval path and the billing model both need confirming against current Microsoft
  documentation, not assumed. Protected Teams APIs have historically required a separate approval
  request to Microsoft *and* carried a metered per-message charge. If either still holds, the
  Teams delta half of this task may not be worth it and only the unattended-auth half survives.
- Read-only. This profile gets no `*.Send`, no `*.ReadWrite`. If it ever needs one, that is a new
  profile and a new decision.

## Requirements

- **Provisioning is a script, not a portal click** — ADR 0003. An idempotent Graph script keyed on
  `displayName`, writing the generated secret straight to Key Vault, converging on a re-run.
  Bicep cannot express Entra objects (no ARM resource provider for app registrations, federated
  credentials, or directory role assignments), which is why this half is a script and the ARM half
  is Bicep. The workspace owner runs the admin-consent step separately.
- **A `client_credentials` grant in `TokenProvider`.** Today's provider does device-code and
  auth-code. This is a third branch, selected per profile by config.
- **Add the profile to the config** and point only `teams_chats` / `teams_channels` at it.
  Everything else keeps its current profile — that is the whole point of per-extractor
  `auth_profile`.
- **Then, and only then, the delta paths**: replace the client-side watermark in both Teams
  extractors with `getAllMessages/delta`, and route `@removed` through `RemovalHandler` so the two
  gaps in `CONTRACTS.md`'s coverage table close.

## Acceptance Criteria

- [ ] The provisioning script is idempotent: two consecutive runs converge, the second creates
      nothing.
- [ ] No secret is written to a file in the repo or printed to a log.
- [ ] The app is scoped to a single-member group, verified by a call that must fail for a
      non-member's mailbox.
- [ ] `forbidden_send_scopes` still holds — the new profile carries no send scope, asserted at
      startup like the others.
- [ ] Teams delta sync replaces the per-poll refetch, measured: Graph calls per cycle before and
      after.
- [ ] `CONTRACTS.md`'s removal-coverage table shows `teams_chats` and `teams_channels` as covered,
      with a test that a `@removed` marker deletes the rendered conversation.

## Non-Goals

- Consolidating the existing three profiles. See above.
- Any write scope.
- The deployed multi-user service (ADR 0010) — this profile is a precondition for it, not a step
  toward it.
