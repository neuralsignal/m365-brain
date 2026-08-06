"""How often you actually talk to someone, turned into a named relationship tier.

Counting is the easy half. The half worth writing down is the ladder: the
script this replaces hardcoded three tiers and then needed a branch that knew
which of them was last, because the bottom tier is the one that never goes
stale. Here the ladder is an ordered list of any length and the terminal rung
says `stale_after_days: null` itself, so no code knows how long the ladder is.

`ops.tiers.interaction_sources` is one fixed join shape applied N times: an
entity type, where the counterparty is read from, where the timestamp is read
from. It is deliberately not a query language -- a fourth source that needs a
different shape is a reason to cut the source, not to widen this.

**Write-back is not implemented, and no longer configurable.** `IndexBackend`
has no per-entity metadata write, and inventing a file-writing path here would
put a second markdown writer in the package. `ops.tiers.write_back` used to
declare it anyway -- one flag that raised on its only interesting value, and
two keys below it that nothing read. `compute_tiers` returns the assignments
and filing them is the consumer's; there is no switch to turn on.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from m365_brain.config.ops import InteractionSourceConfig, TierLevelConfig, TiersConfig
from m365_brain.index.backends.base import IndexBackend
from m365_brain.index.query import parse_timeframe
from m365_brain.model import EntityRef, Observation
from m365_brain.ops.links import indexed_entities
from m365_brain.ops.names import name_key

_MONTH = "1m"
DAYS_PER_MONTH: float = float(parse_timeframe(_MONTH).days)
"""The calendar convention `min_per_month` is measured against.

A **convention, not a threshold**: it converts a count over the lookback window
into a rate, and moving it would silently rescale every rung of every operator's
ladder rather than expressing a policy choice. It is read from the index's own
timeframe parser so that `1m` means the same duration here and in a search
filter, and so that this module states the convention in one place instead of
carrying a second copy of the number.
"""


@dataclass(frozen=True, slots=True)
class TierAssignment:
    """One counterparty's rung, and the counts it was derived from."""

    party: str
    """The counterparty as first written -- an address or a name, not the key."""

    key: str
    """`names.name_key(party)`, the identity the counts were grouped under."""

    interactions: int
    per_month: float
    tier: str
    last_interaction: datetime
    stale: bool


def assign_rung(ladder: Sequence[TierLevelConfig], per_month: float) -> TierLevelConfig | None:
    """The first rung whose `min_per_month` the rate meets, walking in order.

    `None` when the rate is below every rung. That is a property of the ladder
    the operator wrote, not an error: a ladder with no catch-all rung is saying
    that quiet counterparties are not in any tier, and the assignment simply
    does not exist for them.
    """
    for rung in ladder:
        if per_month >= rung.min_per_month:
            return rung
    return None


def is_stale(rung: TierLevelConfig, last_interaction: datetime, now: datetime) -> bool:
    """True when the gap since the last interaction exceeds the rung's tolerance.

    A rung with `stale_after_days: null` is never stale, and that is the whole
    of the special case the ladder needs.
    """
    if rung.stale_after_days is None:
        return False
    return (now - last_interaction).days > rung.stale_after_days


def compute_tiers(backend: IndexBackend, config: TiersConfig, now: datetime, page_size: int) -> list[TierAssignment]:
    """Every counterparty seen inside the lookback window, with its rung.

    `now` is a parameter so the only clock in the call chain belongs to the
    caller. Counterparties below every rung are absent from the result -- see
    `assign_rung`.
    """
    moment = _as_utc(now)
    window_start = moment - timedelta(days=config.lookback_days)
    grouped = _group_by_party(backend, config.interaction_sources, window_start, moment, page_size)

    assignments: list[TierAssignment] = []
    for key, (party, moments) in sorted(grouped.items()):
        per_month = len(moments) / config.lookback_days * DAYS_PER_MONTH
        rung = assign_rung(config.ladder, per_month)
        if rung is None:
            continue
        last = max(moments)
        assignments.append(
            TierAssignment(
                party=party,
                key=key,
                interactions=len(moments),
                per_month=per_month,
                tier=rung.name,
                last_interaction=last,
                stale=is_stale(rung, last, moment),
            )
        )
    return assignments


def _group_by_party(
    backend: IndexBackend,
    sources: Sequence[InteractionSourceConfig],
    window_start: datetime,
    now: datetime,
    page_size: int,
) -> dict[str, tuple[str, list[datetime]]]:
    """`{identity key: (first spelling seen, timestamps)}` across every source."""
    grouped: dict[str, tuple[str, list[datetime]]] = {}
    for source in sources:
        for party, moment in _interactions(backend, source, now, page_size):
            if moment < window_start:
                continue
            key = name_key(party)
            existing = grouped.get(key)
            if existing is None:
                grouped[key] = (party, [moment])
            else:
                existing[1].append(moment)
    return grouped


def _interactions(
    backend: IndexBackend, source: InteractionSourceConfig, now: datetime, page_size: int
) -> list[tuple[str, datetime]]:
    """Every `(counterparty, when)` pair one configured source yields.

    Relations are fetched for the whole entity set in one call rather than per
    entity, matching the traversal in `index/graph.py`: the difference is one
    round-trip against several hundred.
    """
    moments: dict[int, datetime] = {}
    parties: dict[int, list[str]] = {}

    for entity in indexed_entities(backend, source.entity_type, page_size):
        observations = backend.get_observations(entity.entity_id)
        written = _observation_content(observations, source.timestamp.observation)
        if written is None:
            continue
        moment = _parse_moment(entity, source.timestamp.observation, written)
        if source.exclude_future and moment > now:
            continue
        moments[entity.entity_id] = moment
        if source.party_from.observation is not None:
            parties[entity.entity_id] = [
                observation.content
                for observation in observations
                if observation.category == source.party_from.observation
            ]

    if source.party_from.relation is not None:
        for edge in backend.outgoing_relations(sorted(moments)):
            if edge.relation_type == source.party_from.relation:
                parties.setdefault(edge.from_entity_id, []).append(edge.to_name)

    return [(party, moments[entity_id]) for entity_id, written_parties in parties.items() for party in written_parties]


def _observation_content(observations: Sequence[Observation], category: str) -> str | None:
    """The first observation of a category, or None when the entity has none."""
    return next((o.content for o in observations if o.category == category), None)


def _parse_moment(entity: EntityRef, category: str, written: str) -> datetime:
    """An ISO timestamp out of an observation, naming the entity when it is not one."""
    try:
        return _as_utc(datetime.fromisoformat(written))
    except ValueError as exc:
        raise ValueError(f"{entity.permalink}: observation [{category}] is not an ISO timestamp: {written!r}") from exc


def _as_utc(moment: datetime) -> datetime:
    """A naive timestamp is read as UTC.

    Stated rather than assumed: comparing a naive corpus timestamp against an
    aware `now` raises, and refusing every note whose frontmatter omits an
    offset would reject most corpora. UTC is the offset the extractors write.
    """
    return moment if moment.tzinfo is not None else moment.replace(tzinfo=UTC)
