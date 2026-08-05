---
name: m365-brain-ops
description: Run the operational passes over an indexed collection — resolve unresolved person links to entities, compute relationship tiers from interaction frequency, and list inbox messages that have received no reply. Use when tidying up dangling links, ranking contacts by contact frequency, or finding correspondence still awaiting a response.
license: MIT
compatibility: Requires the m365-brain CLI on PATH, M365_BRAIN_CONFIG set to a config file path, and an ops section in that config. Every threshold these verbs use is a key in it — see references/config-keys.md.
allowed-tools: Bash(m365-brain:*) Read
metadata:
  version: "1.0"
  category: "operations"
  homepage: "https://github.com/neuralsignal/m365-brain"
---

# Operational passes

Every command is `m365-brain --config "$M365_BRAIN_CONFIG" …`. Results go to
stdout, logs to stderr; pass `--json` when you intend to parse.

**Nothing here has a tunable buried in it.** Every window, threshold, prefix
list, tier boundary and counting rule is a named key under `ops:` in the config
file. `references/config-keys.md` maps each one. If a number in the output
looks wrong, the answer is a config edit, never a code change — and if you
cannot find the key for a behaviour you are seeing, that is a bug worth
reporting rather than a rule to work around.

## Resolve dangling person links

    m365-brain --config "$M365_BRAIN_CONFIG" ops resolve-links --json

Finds wikilinks that never resolved to an entity and matches them against
entities of the configured target type. Reports; **writes nothing**.

Confidence is derived from the *kind* of match, not from a score you can tune:

| Confidence | Why |
|---|---|
| `high` | an exact identifier or exact title match |
| `medium` | matched after normalisation (case, accents, word order) |
| `unresolved` | no candidate |

A `medium` match is a suggestion for a human, not a fact. Apply it by editing
the file; this verb will not.

Config: `ops.link_resolution.unresolved_prefix`, `ops.link_resolution.target_type`.

## Relationship tiers

    m365-brain --config "$M365_BRAIN_CONFIG" ops tiers --json

Counts interactions per counterparty over `ops.tiers.lookback_days` and assigns
each one a rung of `ops.tiers.ladder`. Reports; **writes nothing**. Setting
`ops.tiers.write_back.enabled: true` is an error rather than a no-op — see
`references/config-keys.md`.

The ladder is an ordered list, and it can have any number of rungs — two, three,
five. There is no code branch that knows which rung is last: the terminal one
says `stale_after_days: null` and means "never goes stale". Adding or removing a
rung is a config edit.

Where the interactions come from is also config: `ops.tiers.interaction_sources`
lists them, each naming an entity type, where to read the counterparty from,
and which observation carries the timestamp. **Those names have to be the ones
your notes actually carry** — a source naming a category nothing writes counts
nothing, and the verb then reports zero counterparties over a full collection,
which reads as a quiet quarter rather than as a fault. Run
`m365-brain --config "$M365_BRAIN_CONFIG" index context --permalink <one item>`
to see what yours are called.

## Inbox triage

    m365-brain --config "$M365_BRAIN_CONFIG" ops triage --timeframe 7d --json

Lists messages in the configured inbox folder, within the timeframe, with no
sent message sharing their conversation, and not already recorded as rejected.

The categories come from `ops.triage.fields` — seven of them, all required and
none defaulted, because the vocabulary inside a note belongs to whoever writes
the notes and guessing `folder` or `date` would work for one collection and
silently return nothing for the next. The `--*-category` options override one
for a single run; the ordinary invocation is the line above.

Two enrichments the source of this pass had are deliberately **absent**:

- **No "has a question" flag.** It was `"?" in body` — one character standing
  in for "this needs a reply". Read the message; a question mark is visible.
- **No word count.** It is a measurement you can take from the file you already
  have.

What ships instead is the part that is a rule rather than a guess: the folder,
the window, the conversation-pairing, the rejection record, whether a message
is a forward (`ops.triage.forward_prefixes`) and whether the recipient was on
cc only.

Config: `ops.triage.own_email`, `inbox_folder`, `sent_folders`, `forward_prefixes`,
`fields.*`. Only the timeframe is an argument — see `references/config-keys.md`
for why, and for what each of the seven field names decides.
