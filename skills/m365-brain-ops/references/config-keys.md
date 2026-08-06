# Every knob these verbs read

The acceptance criterion for this skill is that no threshold, window, prefix
list, tier boundary or counting rule exists anywhere except as a key below. If
you find behaviour you cannot trace to a row in these tables, report it — it is
a defect, not a feature to configure around.

See the `ops:` block of the packaged config template for a worked example.

## `ops resolve-links`

| Key | What it decides |
|---|---|
| `ops.link_resolution.unresolved_prefix` | the slug prefix that marks a link as one of these, e.g. `contact-` |
| `ops.link_resolution.target_types` | every entity type such links may resolve to, e.g. `["contact", "directory_user"]`. A list, because one corpus spells a person more than one way; empty is refused |

Not configurable, and deliberately: the confidence ladder. It is derived from
the *kind* of match — identifier, exact title, normalised title, none — so
there is no number to tune and no way to make a weak match report as a strong
one.

Also not configurable: the name normaliser. Unicode decomposition, `ß`→`ss`,
accent stripping, lowercasing and word-order-insensitive comparison are
deterministic transforms, not policy.

## `ops tiers`

| Key | What it decides |
|---|---|
| `ops.tiers.lookback_days` | how far back interactions are counted |
| `ops.tiers.ladder[].name` | the rung's name, as it appears in the output |
| `ops.tiers.ladder[].min_per_month` | the interactions-per-month floor for that rung |
| `ops.tiers.ladder[].stale_after_days` | days without contact before stale; `null` means never |
| `ops.tiers.interaction_sources[].entity_type` | which indexed entities count as interactions — a type no producer writes counts nothing |
| `ops.tiers.interaction_sources[].party_from` | where the counterparty is read from: one observation category, or one relation type |
| `ops.tiers.interaction_sources[].timestamp` | the observation category carrying the timestamp |
| `ops.tiers.interaction_sources[].exclude_future` | whether to drop timestamps in the future |

**The ladder is ordered and any length.** Two rungs, three, five — the code
walks it and takes the first rung whose floor is met. Nothing knows which rung
is last; the terminal one says `stale_after_days: null` and that is the whole
of the "never stale" rule.

`interaction_sources` is one fixed join shape with three declarative fields. If
a source you need does not fit it, the right answer is to leave that source out
rather than to widen this into a query language.

**Write-back is not implemented, and is not configurable.** The index has no
per-entity metadata write, so writing tiers into frontmatter would mean a second
markdown writer inside the package. `ops.tiers.write_back` used to declare it
anyway — one flag that raised on its only interesting value, and two keys below
it that nothing read. A schema is the one place documentation must not live,
because a reader cannot tell it from a switch. The subtree is gone; the verb
prints the assignments and filing them is the caller's.

Rate conversion uses a fixed 30-day month, taken from the same timeframe parser
the index uses so there is one definition of "a month" in the package. It is a
calendar convention, not a threshold — there is nothing to tune, and a
different month length would not change which rung anything lands on except at
the boundary.

## `ops triage`

| Key | What it decides |
|---|---|
| `ops.triage.own_email` | the address that identifies the recipient; drives the cc-only check |
| `ops.triage.inbox_folder` | the folder a message must be in to be considered |
| `ops.triage.sent_folders` | the folders searched for a reply in the same conversation |
| `ops.triage.forward_prefixes` | subject prefixes that mark a forward, e.g. `["fw:", "fwd:", "wg:"]` |
| `ops.triage.fields.entity_type` | the entity type your messages carry |
| `ops.triage.fields.folder` | the observation category holding the folder |
| `ops.triage.fields.conversation_id` | the category holding the thread id — how a reply is paired |
| `ops.triage.fields.message_id` | the category holding the message id — what a rejected draft points at |
| `ops.triage.fields.sender` | the category holding the sender |
| `ops.triage.fields.recipients` | the category holding the `to` line |
| `ops.triage.fields.timestamp` | the category holding when it arrived |

The timeframe is a command-line argument, not config: it is a property of the
question being asked, and the same collection is legitimately triaged over a
day and over a month.

**The seven `fields` names are required and none is defaulted.** They state what
your notes contain rather than what the library prefers, and hardcoding `folder`
or `date` would bake one collection's vocabulary in: a wrong threshold produces a
visibly wrong report, a guessed category produces an empty one, and an empty
report reads exactly like an inbox with nothing owing. `--entity-type`,
`--folder-category`, `--conversation-category`, `--message-id-category`,
`--sender-category`, `--recipients-category` and `--timestamp-category`
**override** one of them for a single run; the ordinary invocation passes none.

`conversation_id` and `message_id` are different identifier spaces and both are
read: pairing a reply with the message it answers is a *thread* comparison,
while a draft the human deleted names a single *message*.

Rejected messages come from the outbox's own rejection record, written by the
reconciliation pass. There is no separate state file, and adding one would put
the same fact in two places.

## Deliberately absent

| Not shipped | Why |
|---|---|
| a "has a question" flag | it was `"?" in body`; a single character cannot support the conclusion the field name asserts, and a reader can see a question mark |
| a body word count | a measurement the caller can take from the file it already has |
| a sparseness scorer | five weighted dimensions and an excluded-subtree list are a *definition* of "sparse", not a threshold; a config key per dimension would ship the definition with a knob attached |
