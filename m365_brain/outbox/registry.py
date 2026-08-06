"""Which outboxes exist, what authority each runs at, and who executes it.

Built from config at startup and handed its handlers, so there is no
module-level mutable singleton and no import-time registration side effect.
That is not a style preference: the implementation this replaces registered
into a module-level `REGISTRY` that nothing ever populated, injected the empty
singleton into its worker, and consequently rejected 100% of production intents
with "no outbox registered". A registry you must remember to fill is a registry
that ships empty.

Both authority guards run here, at build, and both crash the process. A
`draft_only` outbox that could send mail is not an intent to route around; it
is a configuration that cannot be true.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from m365_brain.config import AuthProfileConfig, OutboxesConfig
from m365_brain.outbox.authority import Authority, AuthorityViolation
from m365_brain.vault.dispatch import DRAFT_ONLY_OPS, GraphOp, OutboxHandler


class OutboxConfigurationError(Exception):
    """Config and handlers disagree about which outboxes exist."""


class UnknownOutbox(Exception):
    """An intent named an outbox the registry does not hold."""


@dataclass(frozen=True)
class RegisteredOutbox:
    """One outbox: its policy from config, its behaviour from a handler."""

    name: str
    authority: Authority
    auth_profile: str
    handler: OutboxHandler


class OutboxRegistry:
    """Name -> outbox. Immutable once built."""

    def __init__(self, entries: Mapping[str, RegisteredOutbox]) -> None:
        self._entries = dict(entries)

    def names(self) -> list[str]:
        return sorted(self._entries)

    def get(self, name: str) -> RegisteredOutbox:
        try:
            return self._entries[name]
        except KeyError:
            raise UnknownOutbox(f"no outbox named {name!r}; registered: {self.names()}") from None


def build_registry(
    config: OutboxesConfig,
    profiles: Mapping[str, AuthProfileConfig],
    handlers: Mapping[str, OutboxHandler],
) -> OutboxRegistry:
    """Pair each configured outbox with its handler, running both authority guards.

    Handlers are injected rather than imported. The executors live in the
    Microsoft 365 half of the package, which is this module's peer in the layer
    map, so the wiring is the caller's -- and that is also what makes the whole
    lifecycle testable with no Graph in sight.
    """
    _reject_mismatched_sets(config, handlers)
    entries: dict[str, RegisteredOutbox] = {}
    for name, definition in config.definitions.items():
        authority = Authority(definition.authority)
        handler = handlers[name]
        if authority is Authority.DRAFT_ONLY:
            _guard_declared_ops(name, handler)
            _guard_granted_scopes(name, definition.auth_profile, profiles, config.forbidden_send_scopes)
        entries[name] = RegisteredOutbox(
            name=name, authority=authority, auth_profile=definition.auth_profile, handler=handler
        )
    return OutboxRegistry(entries)


def _reject_mismatched_sets(config: OutboxesConfig, handlers: Mapping[str, OutboxHandler]) -> None:
    configured = set(config.definitions)
    supplied = set(handlers)
    missing = sorted(configured - supplied)
    extra = sorted(supplied - configured)
    if missing:
        raise OutboxConfigurationError(
            f"outboxes.definitions names outboxes with no handler: {missing}. "
            "An outbox nothing can execute would reject every intent written to it."
        )
    if extra:
        raise OutboxConfigurationError(
            f"handlers were supplied for outboxes absent from outboxes.definitions: {extra}. "
            "A handler with no configured authority has no permission policy at all."
        )


def _guard_declared_ops(name: str, handler: OutboxHandler) -> None:
    """Guard 1: what the handler says it may do must fit inside the authority."""
    excess = sorted(GraphOp(op).value for op in handler.declared_ops if op not in DRAFT_ONLY_OPS)
    if excess:
        raise AuthorityViolation(
            f"outbox {name!r} is authority draft_only but its handler declares {excess}. "
            f"draft_only permits only {sorted(op.value for op in DRAFT_ONLY_OPS)}."
        )


def _guard_granted_scopes(
    name: str,
    profile_name: str,
    profiles: Mapping[str, AuthProfileConfig],
    forbidden: list[str],
) -> None:
    """Guard 2: the app a draft-only outbox uses must not hold a send scope.

    This is the one that catches the realistic failure: nobody edits a handler
    to start sending mail, but somebody widens an app's scopes for an unrelated
    workload and a later bug then can.
    """
    profile = profiles.get(profile_name)
    if profile is None:
        raise OutboxConfigurationError(
            f"outbox {name!r} names auth profile {profile_name!r}, which is not in auth.profiles "
            f"(configured: {sorted(profiles)})"
        )
    blocked = {scope.casefold() for scope in forbidden}
    granted = sorted(scope for scope in profile.scopes if scope.casefold() in blocked)
    if granted:
        raise AuthorityViolation(
            f"outbox {name!r} is authority draft_only but its auth profile {profile_name!r} is granted "
            f"{granted}. Remove the scope, or change the authority -- not both halves of a contradiction."
        )
