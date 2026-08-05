"""The `ops:` section -- link resolution, relationship tiers, and inbox triage.

Every threshold, window, prefix list and tier boundary the bundled operational
commands use is named here. The rule that produced this section: a heuristic
that cannot be expressed as config does not ship, so anything absent from these
models is absent from the code too.

The tier ladder is an ordered list rather than three numbered tiers with a
"the last one is never stale" branch. N tiers, no special case: the terminal
entry says `stale_after_days: null` and means it.
"""

from __future__ import annotations

from pydantic import BaseModel, model_validator

from m365_brain.config.base import SECTION_MODEL_CONFIG


class LinkResolutionConfig(BaseModel):
    model_config = SECTION_MODEL_CONFIG
    unresolved_prefix: str
    target_type: str


class TierLevelConfig(BaseModel):
    model_config = SECTION_MODEL_CONFIG
    name: str
    min_per_month: float
    stale_after_days: int | None
    """How long without contact counts as stale.

    `null` is meaningful and is a required spelling: the terminal rung of the
    ladder never goes stale, and saying so beats a code branch that knows which
    rung is last.
    """


class PartySelector(BaseModel):
    """Where an interaction's counterparty is read from.

    Exactly one of the two is set -- an observation category, or a relation
    type. Both or neither is a config error.
    """

    model_config = SECTION_MODEL_CONFIG
    observation: str | None
    relation: str | None

    @model_validator(mode="after")
    def _exactly_one(self) -> PartySelector:
        if (self.observation is None) == (self.relation is None):
            raise ValueError(
                "ops.tiers.interaction_sources[].party_from must set exactly one of "
                "'observation' or 'relation'; the other is null"
            )
        return self


class TimestampSelector(BaseModel):
    model_config = SECTION_MODEL_CONFIG
    observation: str


class InteractionSourceConfig(BaseModel):
    model_config = SECTION_MODEL_CONFIG
    entity_type: str
    party_from: PartySelector
    timestamp: TimestampSelector
    exclude_future: bool


class TierWriteBackConfig(BaseModel):
    model_config = SECTION_MODEL_CONFIG
    enabled: bool
    fields: dict[str, str]
    """Computed value name -> frontmatter key it is written to.

    A mapping rather than a list, so an operator whose notes call it
    `contact_tier` does not have to call it `tier`.
    """

    create_missing: bool


class TiersConfig(BaseModel):
    model_config = SECTION_MODEL_CONFIG
    lookback_days: int
    ladder: list[TierLevelConfig]
    interaction_sources: list[InteractionSourceConfig]
    write_back: TierWriteBackConfig

    @model_validator(mode="after")
    def _ladder_is_named_and_unique(self) -> TiersConfig:
        if not self.ladder:
            raise ValueError("ops.tiers.ladder must have at least one rung")
        names = [rung.name for rung in self.ladder]
        duplicates = sorted({name for name in names if names.count(name) > 1})
        if duplicates:
            raise ValueError(f"ops.tiers.ladder names must be unique -- duplicated: {duplicates}")
        return self


class TriageConfig(BaseModel):
    model_config = SECTION_MODEL_CONFIG
    own_email: str
    inbox_folder: str
    sent_folders: list[str]
    forward_prefixes: list[str]


class OpsConfig(BaseModel):
    model_config = SECTION_MODEL_CONFIG
    link_resolution: LinkResolutionConfig
    tiers: TiersConfig
    triage: TriageConfig
