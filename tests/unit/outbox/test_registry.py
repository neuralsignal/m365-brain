"""Both tier guards, and both directions of a config/handler mismatch.

The guards fire at build -- process start -- so every test here asserts that
`build_registry` raises, not that a dispatch was refused. A `draft_only` outbox
that could send mail must never reach the point of having intents routed to it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from m365_brain.outbox.registry import OutboxConfigurationError, UnknownOutbox, build_registry
from m365_brain.outbox.tiers import Tier, TierViolation
from m365_brain.vault.dispatch import DRAFT_ONLY_OPS, DispatchResult, GraphOp, OutboxHandler
from m365_brain.vault.intent import IntentEnvelope


@dataclass
class FakeHandler:
    """Structurally an `OutboxHandler`; imports nothing to say so."""

    name: str
    declared_ops: frozenset[GraphOp] = field(default_factory=lambda: DRAFT_ONLY_OPS)

    def execute(self, envelope: IntentEnvelope) -> DispatchResult:
        return DispatchResult(graph_message_id=f"MSG-{envelope.uuid}")


def _handlers(**overrides) -> dict[str, OutboxHandler]:
    handlers = {
        "email.draft": FakeHandler("email.draft"),
        "teams.post_message": FakeHandler("teams.post_message", frozenset({GraphOp.POST_CHANNEL})),
    }
    handlers.update(overrides)
    return handlers


def test_a_handler_satisfies_the_protocol_without_importing_it():
    assert isinstance(FakeHandler("x"), OutboxHandler)


class TestHappyPath:
    def test_config_supplies_the_tier_and_the_handler_supplies_the_behaviour(self, outboxes_config, auth_profiles):
        registry = build_registry(outboxes_config, auth_profiles, _handlers())

        assert registry.names() == ["email.draft", "teams.post_message"]
        assert registry.get("email.draft").tier is Tier.DRAFT_ONLY
        assert registry.get("teams.post_message").tier is Tier.AUTO_SEND
        assert registry.get("email.draft").auth_profile == "mail"

    def test_an_unregistered_name_raises_naming_what_is_registered(self, outboxes_config, auth_profiles):
        registry = build_registry(outboxes_config, auth_profiles, _handlers())

        with pytest.raises(UnknownOutbox) as excinfo:
            registry.get("file.update")

        assert "email.draft" in str(excinfo.value)


class TestSetMismatch:
    def test_a_configured_outbox_with_no_handler_is_a_config_error(self, outboxes_config, auth_profiles):
        handlers = _handlers()
        del handlers["teams.post_message"]

        with pytest.raises(OutboxConfigurationError) as excinfo:
            build_registry(outboxes_config, auth_profiles, handlers)

        assert "teams.post_message" in str(excinfo.value)

    def test_a_handler_with_no_configured_tier_is_a_config_error(self, outboxes_config, auth_profiles):
        """A handler nobody gave a tier has no permission policy at all, which
        is worse than one with the wrong policy: nothing would check it."""
        handlers = _handlers(**{"file.update": FakeHandler("file.update", frozenset({GraphOp.PUT_FILE}))})

        with pytest.raises(OutboxConfigurationError) as excinfo:
            build_registry(outboxes_config, auth_profiles, handlers)

        assert "file.update" in str(excinfo.value)


class TestGuardOne:
    def test_a_draft_only_handler_declaring_send_mail_stops_the_process(self, outboxes_config, auth_profiles):
        handlers = _handlers(
            **{"email.draft": FakeHandler("email.draft", frozenset({GraphOp.CREATE_DRAFT, GraphOp.SEND_MAIL}))}
        )

        with pytest.raises(TierViolation) as excinfo:
            build_registry(outboxes_config, auth_profiles, handlers)

        assert "send_mail" in str(excinfo.value)

    def test_a_declaration_inside_the_tier_is_fine(self, outboxes_config, auth_profiles):
        handlers = _handlers(**{"email.draft": FakeHandler("email.draft", frozenset({GraphOp.CREATE_DRAFT}))})

        assert build_registry(outboxes_config, auth_profiles, handlers).names()

    def test_an_auto_send_outbox_may_declare_anything(self, outboxes_config, auth_profiles):
        handlers = _handlers(
            **{"teams.post_message": FakeHandler("teams.post_message", frozenset({GraphOp.SEND_MAIL}))}
        )

        assert build_registry(outboxes_config, auth_profiles, handlers).names()


class TestGuardTwo:
    def test_a_send_scope_on_a_draft_only_profile_stops_the_process(self, outboxes_config, auth_profiles):
        """The realistic failure: nobody edits the handler, somebody widens the
        app's scopes for another workload."""
        auth_profiles["mail"] = auth_profiles["mail"].model_copy(update={"scopes": ["Mail.ReadWrite", "Mail.Send"]})

        with pytest.raises(TierViolation) as excinfo:
            build_registry(outboxes_config, auth_profiles, _handlers())

        assert "Mail.Send" in str(excinfo.value)

    def test_the_scope_comparison_ignores_case(self, outboxes_config, auth_profiles):
        auth_profiles["mail"] = auth_profiles["mail"].model_copy(update={"scopes": ["mail.send"]})

        with pytest.raises(TierViolation):
            build_registry(outboxes_config, auth_profiles, _handlers())

    def test_the_forbidden_list_is_config_not_a_constant(self, outboxes_config, auth_profiles):
        """Tenant policy decides which scope is disqualifying, so an empty list
        is a legitimate (if unwise) configuration and must not be overridden."""
        permissive = outboxes_config.model_copy(update={"forbidden_send_scopes": []})
        auth_profiles["mail"] = auth_profiles["mail"].model_copy(update={"scopes": ["Mail.Send"]})

        assert build_registry(permissive, auth_profiles, _handlers()).names()

    def test_a_missing_auth_profile_is_a_config_error(self, outboxes_config, auth_profiles):
        del auth_profiles["mail"]

        with pytest.raises(OutboxConfigurationError) as excinfo:
            build_registry(outboxes_config, auth_profiles, _handlers())

        assert "mail" in str(excinfo.value)
