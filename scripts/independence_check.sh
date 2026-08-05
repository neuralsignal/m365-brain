#!/usr/bin/env bash
# AC-3 against a real tenant: drive the whole loop from the CLI alone, in a
# scratch directory, with nothing from this repo present but the wheel.
#
# The hermetic version of this check is `tests/integration/test_independence.py`
# and runs in CI. This one exists for the half that cannot be automated: the
# device-code login, and the question of whether the *installed wheel* -- not the
# source tree -- carries everything the loop needs.
#
#   REPO=/path/to/m365-brain bash scripts/independence_check.sh
#
# Requires: python3, jq, and an Entra app whose client and tenant ids are in the
# environment. Nothing else, and deliberately nothing from the repo.
set -euo pipefail

: "${REPO:?set REPO to the repository root}"
: "${MSAL_CLIENT_ID:?}"
: "${MSAL_TENANT_ID:?}"

SCRATCH="$(mktemp -d)"
trap 'echo; echo "scratch kept at: $SCRATCH"' EXIT
cd "$SCRATCH"

step() { echo; echo "── $* ─────────────────────────────────────────"; }

step "install the wheel into a clean virtualenv"
python3 -m venv .venv
# shellcheck source=/dev/null
. .venv/bin/activate
pip install --quiet "m365-brain[vector] @ file://${REPO}"
command -v m365-brain

step "1. config and vault from nothing"
m365-brain init ./m365-brain.yaml --vault ./vault
test -f ./m365-brain.yaml
for area in inbox annotations outbox _meta; do test -d "./vault/${area}"; done

cat > .env <<EOF
MSAL_CLIENT_ID=${MSAL_CLIENT_ID}
MSAL_TENANT_ID=${MSAL_TENANT_ID}
M365_MAIL_CLIENT_ID=${M365_MAIL_CLIENT_ID:-$MSAL_CLIENT_ID}
M365_FILES_CLIENT_ID=${M365_FILES_CLIENT_ID:-$MSAL_CLIENT_ID}
M365_CHAT_CLIENT_ID=${M365_CHAT_CLIENT_ID:-$MSAL_CLIENT_ID}
M365_OWN_EMAIL=${M365_OWN_EMAIL:-nobody@example.com}
EOF

m365-brain --config ./m365-brain.yaml config validate
m365-brain --config ./m365-brain.yaml config show --json | jq -e '.vault.root'
m365-brain --config ./m365-brain.yaml config show --json | jq -e '.auth.client_secret == null or .auth.client_secret == "***"'
m365-brain --config ./m365-brain.yaml vault path inbox --extractor email

step "2. authenticate -- interactive, by nature"
echo "run this in another shell, then come back:"
echo "  cd $SCRATCH && . .venv/bin/activate && m365-brain --config ./m365-brain.yaml auth login --profile default"
read -r -p "press enter once the device-code flow has completed "
m365-brain --config ./m365-brain.yaml auth status --json | jq -e '[.profiles[] | select(.valid)] | length > 0'

step "3. extract and index"
m365-brain --config ./m365-brain.yaml extract --only email --dry-run
m365-brain --config ./m365-brain.yaml run --once --only email,calendar,index --json > cycle.json
jq -e '.ok == true'                     cycle.json
jq -e '.extractors | length == 2'       cycle.json
jq -e '.index != null'                  cycle.json
test -f ./vault/_meta/manifests/latest.json
m365-brain --config ./m365-brain.yaml status --json | jq -e '.units.email.last_success_at'

step "4. search what landed"
m365-brain --config ./m365-brain.yaml index search "meeting" --json | jq -e '.total >= 0'
m365-brain --config ./m365-brain.yaml index search "meeting" --search-type hybrid --json > /dev/null
m365-brain --config ./m365-brain.yaml index recent --timeframe 7d --json | jq -e '.entities'

step "5. a hook receives the manifest"
cat > probe.py <<'PY'
import json, pathlib
def on_cycle(manifest):
    pathlib.Path("hook_saw.json").write_text(json.dumps(manifest.paths(kind="added", extractor=None)))
PY
python3 - <<'PY'
import pathlib
p = pathlib.Path("m365-brain.yaml")
p.write_text(p.read_text().replace("post_cycle: []", 'post_cycle: ["probe:on_cycle"]'))
PY
PYTHONPATH="$PWD" m365-brain --config ./m365-brain.yaml run --once --only calendar
test -f hook_saw.json

step "6. write a draft by hand, push it, reconcile it"
UUID="$(python3 -c 'import uuid; print(uuid.uuid4())')"
mkdir -p ./vault/outbox/email.draft
cat > "./vault/outbox/email.draft/${UUID}.md" <<EOF
---
uuid: ${UUID}
schema_version: 1
created_at: 2026-08-05T09:00:00Z
created_by: independence-check
payload:
  kind: email.draft
  mailbox: me
  to: ["${M365_OWN_EMAIL:-nobody@example.com}"]
  cc: null
  bcc: null
  subject: independence check
  attachments: null
  inline_images: null
  include_signature: false
  revises_message_id: null
---
Written by the CLI gate. Delete it.
EOF

m365-brain --config ./m365-brain.yaml outbox list --json | jq -e '[.intents[] | select(.status == "pending")] | length == 1'
m365-brain --config ./m365-brain.yaml outbox push --json | jq -e '.dispatched == 1'
m365-brain --config ./m365-brain.yaml outbox reconcile --json | jq -e '.outcomes'

step "7. nothing here names any consuming workspace"
if grep -rIn -iE '(^|[^[:alnum:]_-])brain\b|sanoptis' . \
     --exclude-dir=.venv --exclude-dir=vault \
   | grep -viE 'm365[-_]brain' | grep -q .; then
  echo "FAIL: consuming-workspace vocabulary found above"
  exit 1
fi

echo
echo "AC-3 PASS"
