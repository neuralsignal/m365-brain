# Quickstart

One path, start to finish: install, configure, authenticate, run a cycle,
search what landed, write something back, and wire a hook. No Python required
after the install.

Every command is `m365-brain --config <path>`. The single exception is `init`,
which creates the config file and so cannot require it to exist.

## 1. Install

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install "m365-brain[vector,convert]"
m365-brain --help
```

Extras: `vector` for semantic search, `convert` for PDF/DOCX/XLSX conversion,
`azure` for blob storage. The core install does extraction, indexing and
write-back without any of them.

## 2. Create a config and a vault

```bash
m365-brain init ./m365-brain.yaml --vault ./vault
```

That writes a complete, commented config file — **the file is the reference**;
every key the library reads is in it with a note on what it does — and creates
`inbox/`, `annotations/`, `outbox/`, `attachments/` and `_meta/` under the
vault. Paths in the file point at the vault you named.

`init` refuses to overwrite an existing config. A config file is the one
artifact you edited by hand.

## 3. Fill in the environment

The config references environment variables as `${NAME}`. A referenced variable
that is not set is a load failure, not an empty string.

```bash
cat > .env <<'EOF'
MSAL_CLIENT_ID=<your Entra app's client id>
MSAL_TENANT_ID=<your tenant id>
M365_MAIL_CLIENT_ID=<the mail app's client id>
M365_FILES_CLIENT_ID=<the files app's client id>
M365_CHAT_CLIENT_ID=<the chat app's client id>
M365_OWN_EMAIL=<your address>
EOF
```

`.env` is read from the config file's directory first, then from the working
directory. One Entra app for everything is fine to start: point all five ids at
the same app and give it the union of the scopes.

## 4. Validate before you run anything

```bash
m365-brain --config ./m365-brain.yaml config validate
m365-brain --config ./m365-brain.yaml config show --json | jq .vault.root
```

`config validate` parses every file **and imports every configured hook**. That
is what makes it a preflight rather than a YAML syntax check: a typo in a hook
path fails here in under a second instead of four hours into a SharePoint pass.
Exit 3 means fix the config.

`config show` redacts secrets. It prints the merged, env-expanded,
path-resolved configuration exactly as the library sees it — which is the
answer to "why is it writing *there*".

## 5. Authenticate

```bash
m365-brain --config ./m365-brain.yaml auth login --profile mail
m365-brain --config ./m365-brain.yaml auth status --json
```

Device code: it prints a code and a URL, you finish in a browser. `auth status`
never prompts; it reports `authenticated`, `expired` or `never_authenticated`
per profile and exits 4 if any profile has no usable token.

Profiles exist so scopes are not pooled. The app that may post to a channel is
not the app that reads mail, which is what turns "this outbox can only draft"
from a permission into a promise.

## 6. Probe, then run

```bash
m365-brain --config ./m365-brain.yaml extract --dry-run
m365-brain --config ./m365-brain.yaml run --once
```

`--dry-run` validates the token and probes each enabled extractor's endpoint
without writing a file — it is how you find a missing scope before it costs you
a pass.

`run --once` does one cycle: extract, record, index, then dispatch the
post-cycle hooks. Continuous mode is the same command without `--once`; put it
under whatever you already use to supervise processes. There is no daemon to
install.

```bash
m365-brain --config ./m365-brain.yaml run --once --only email,calendar --json
m365-brain --config ./m365-brain.yaml run --once --resync   # forget delta tokens first
```

## 7. Read the manifest

Every cycle writes `<vault>/_meta/manifests/<cycle id>.json` and copies it to
`latest.json`. That file is a complete record of what the cycle wrote — which
is what lets a consumer stop scanning the vault and stop keeping its own
watermark file.

```bash
jq '.ok, (.extractors | map(.name))' ./vault/_meta/manifests/latest.json
m365-brain --config ./m365-brain.yaml status --json
```

`status` reports per-unit last run, last success and failure streak. The two
timestamps are separate on purpose: a failing extractor advances `last_run_at`
so it stops hammering a broken endpoint, and holds `last_success_at` back so
the staleness stays visible.

## 8. Search

```bash
m365-brain --config ./m365-brain.yaml index search "budget" --json
m365-brain --config ./m365-brain.yaml index search "budget" --search-type hybrid --json
m365-brain --config ./m365-brain.yaml index recent --timeframe 7d --json
m365-brain --config ./m365-brain.yaml index context --permalink email-2026-03-04-q1-budget-review-a1b2c3
```

Results go to stdout; logs go to stderr. `--json` output never has to be
separated from log noise first.

## 9. Write something back

An intent is a markdown file in an outbox directory. That *is* the interface —
there is no `outbox new` verb, because a second way to write the same bytes is
a second thing to keep in step.

```bash
UUID=$(python3 -c 'import uuid; print(uuid.uuid4())')
mkdir -p ./vault/outbox/email.draft
cat > "./vault/outbox/email.draft/${UUID}.md" <<EOF
---
uuid: ${UUID}
schema_version: 1
created_at: 2026-08-05T09:00:00Z
created_by: quickstart
payload:
  kind: email.draft
  mailbox: me
  to: ["someone@example.com"]
  cc: null
  bcc: null
  subject: hello
  attachments: null
  inline_images: null
  include_signature: false
  revises_message_id: null
---
The markdown body **is** the email body. There is no frontmatter \`body:\` key.
EOF

m365-brain --config ./m365-brain.yaml outbox list --json
m365-brain --config ./m365-brain.yaml outbox push
m365-brain --config ./m365-brain.yaml outbox reconcile --json
```

**Every field is required and unknown keys are rejected.** A half-filled intent
is archived with a receipt explaining exactly which field was missing, rather
than dispatched with invented values.

`push` claims, routes, dispatches, writes a receipt and archives — once per
intent. A dispatched uuid is never dispatched twice, and an in-flight intent is
never retried automatically, because retrying an unknown send duplicates mail.

`reconcile` asks Graph what became of each dispatched draft: still sitting
there, sent as written, sent after edits, or deleted.

A Teams channel post needs a `(team_id, channel_id)` pair nobody types from
memory, so there is one authoring verb for it:

```bash
m365-brain --config ./m365-brain.yaml teams post \
  --channel-url 'https://teams.microsoft.com/l/channel/19%3A...%40thread.tacv2/General?groupId=...' \
  --body-file ./message.html --created-by quickstart
```

It writes the intent file and stops. `outbox push` remains the only path that
sends anything.

## 10. Wire a hook

A hook is a callable that receives the manifest after each cycle.

```python
# my_package/hooks.py
def on_cycle(manifest):
    for path in manifest.paths(kind="added", extractor="email"):
        print("new mail:", path)
```

```yaml
hooks:
  post_cycle:
    - "my_package.hooks:on_cycle"
```

The spec is `module.path:callable` — the colon is required, because `a.b.c`
cannot say whether `c` is a submodule or an attribute.

Resolution happens at startup, not at first fire: an unimportable hook, a
missing attribute, a non-callable, or a signature that cannot take one
positional argument all fail before any extractor touches Graph.

A hook that raises is **logged with its full traceback, recorded on the
manifest, and does not abort the cycle** — the remaining hooks still run. It
does make `manifest.ok` false, so `run --once` exits 1 and `status` keeps
reporting it. Fail-soft is not swallowed: nothing about the outcome claims
success.

There is no hook timeout. A thread-based one cannot stop a blocked callable —
it produces a lying log line and a leaked thread. Your callable, your
behaviour.

## Exit codes

`CONTRACTS.md` holds the authoritative table. In short:

| Code | Meaning | What to do |
|---|---|---|
| 0 | success | — |
| 1 | an extractor, the index step, a hook, a push or a reconcile failed | retry, or read the log |
| 2 | usage error | fix the command line |
| 3 | configuration invalid or a name that does not resolve | fix the config |
| 4 | authentication required or expired beyond refresh | `auth login --profile …` |

3 and 4 are separate from 1 so a supervisor can tell "you typed it wrong" and
"go re-login" apart from "Graph is down" without scraping a message.

## Verifying an installation end to end

`scripts/independence_check.sh` drives this entire page against a real tenant,
in a scratch directory, from a wheel — nothing from a source checkout:

```bash
REPO=/path/to/m365-brain bash scripts/independence_check.sh
```

It stops once, at the device-code login, and prints the command to run. The
hermetic half of the same check is `tests/integration/test_independence.py` and
runs in CI on every commit.

**Verified:** 2026-08-05, against the hermetic harness at package version 1.1.1.
Two defects surfaced on the first run and were fixed rather than worked around:
`init` wrote a template whose paths did not match its own `--vault`, and the
template configured an outbox named `chat.post_message` that no executor
implements (`teams.post_message`). The real-tenant run of
`independence_check.sh` has not been executed yet — it needs a tenant.
