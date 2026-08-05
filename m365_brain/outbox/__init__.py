"""The outbox lifecycle: claim, route by tier, dispatch, receipt, archive.

Knows nothing about Microsoft Graph. Handlers arrive injected and satisfy a
Protocol declared in `vault`, so this half of the package is exercisable end to
end with no transport present -- which is also why the layer map forbids it
from importing `m365` at all.
"""

from m365_brain.outbox.filesystem_store import FilesystemIntentStore
from m365_brain.outbox.registry import (
    OutboxConfigurationError,
    OutboxRegistry,
    RegisteredOutbox,
    UnknownOutbox,
    build_registry,
)
from m365_brain.outbox.stores import (
    InMemoryIntentStore,
    IntentAlreadyClaimed,
    IntentNotClaimed,
    IntentStore,
)
from m365_brain.outbox.tiers import Action, IntentStatus, Tier, TierRouter, TierRoutingError, TierViolation

__all__ = [
    "Action",
    "FilesystemIntentStore",
    "InMemoryIntentStore",
    "IntentAlreadyClaimed",
    "IntentNotClaimed",
    "IntentStatus",
    "IntentStore",
    "OutboxConfigurationError",
    "OutboxRegistry",
    "RegisteredOutbox",
    "Tier",
    "TierRouter",
    "TierRoutingError",
    "TierViolation",
    "UnknownOutbox",
    "build_registry",
]
