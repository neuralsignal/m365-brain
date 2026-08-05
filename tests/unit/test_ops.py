"""The three operational reports, and the ladder that has no special case.

The tests that matter most here are the ones that would pass trivially if the
ported scripts had been copied instead of rewritten: a ladder of two, three and
five rungs going through the same code path, and a threshold moving purely in
config.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from hypothesis import given
from hypothesis import strategies as st

from m365_brain.config import (
    ConfigError,
    InteractionSourceConfig,
    LinkResolutionConfig,
    PartySelector,
    TierLevelConfig,
    TiersConfig,
    TierWriteBackConfig,
    TimestampSelector,
    TriageConfig,
)
from m365_brain.index.backends.memory import InMemoryIndexBackend
from m365_brain.model import Entity, Observation, Relation
from m365_brain.ops.links import resolve_links
from m365_brain.ops.names import (
    deslugify,
    email_addresses,
    name_key,
    normalize_name,
    reverse_comma_name,
)
from m365_brain.ops.tiers import assign_rung, compute_tiers, is_stale
from m365_brain.ops.triage import MessageFields, is_cc_only, is_forward, rejected_references, triage
from m365_brain.outbox.stores import InMemoryIntentStore
from m365_brain.vault.dispatch import DispatchReceipt
from m365_brain.vault.intent import IntentEnvelope, dump_intent
from m365_brain.vault.payloads import EmailReplyPayload

PAGE_SIZE = 2
"""Deliberately smaller than any fixture, so every listing pages at least once."""

NOW = datetime(2026, 8, 1, tzinfo=UTC)
STAMP = "2026-01-01T00:00:00Z"

LATIN = st.text(alphabet=st.characters(min_codepoint=97, max_codepoint=122), min_size=1, max_size=6)


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


def entity(key: str, title: str, entity_type: str, observations=(), relations=()) -> Entity:
    return Entity(
        key=key,
        root_name="corpus",
        file_path=f"{key}.md",
        title=title,
        entity_type=entity_type,
        permalink=key,
        tags=[],
        aliases=[],
        content=title,
        checksum=key,
        metadata={},
        created_at=STAMP,
        updated_at=STAMP,
        observations=list(observations),
        relations=list(relations),
    )


def observation(category: str, content: str) -> Observation:
    return Observation(category=category, content=content, tags=[], context=None)


def relation(relation_type: str, to_name: str) -> Relation:
    return Relation(relation_type=relation_type, to_name=to_name, to_entity_id=None, context=None)


@pytest.fixture()
def backend(index_config):
    store = InMemoryIndexBackend(index_config)
    store.initialize()
    return store


def loaded(store: InMemoryIndexBackend, entities) -> InMemoryIndexBackend:
    """Upsert and rebuild, which is what makes the entities listable."""
    store.upsert_entities(list(entities))
    store.rebuild_text_index()
    return store


def ladder(*rungs: tuple[str, float, int | None]) -> list[TierLevelConfig]:
    return [TierLevelConfig(name=name, min_per_month=minimum, stale_after_days=stale) for name, minimum, stale in rungs]


def tiers_config(rungs: list[TierLevelConfig], sources: list[InteractionSourceConfig], lookback_days: int):
    return TiersConfig(
        lookback_days=lookback_days,
        ladder=rungs,
        interaction_sources=sources,
        write_back=TierWriteBackConfig(enabled=False, fields={}, create_missing=False),
    )


def observation_source(entity_type: str, party: str, stamp: str, exclude_future: bool) -> InteractionSourceConfig:
    return InteractionSourceConfig(
        entity_type=entity_type,
        party_from=PartySelector(observation=party, relation=None),
        timestamp=TimestampSelector(observation=stamp),
        exclude_future=exclude_future,
    )


# ---------------------------------------------------------------------------
# names
# ---------------------------------------------------------------------------


class TestNames:
    def test_word_order_is_not_part_of_the_key(self):
        assert normalize_name("Anna Meier") == normalize_name("Meier Anna") == "anna meier"

    def test_case_is_not_part_of_the_key(self):
        assert normalize_name("ANNA MEIER") == "anna meier"

    def test_a_comma_form_reverses_before_it_normalises(self):
        assert reverse_comma_name("Meier, Anna") == "Anna Meier"
        assert name_key("Meier, Anna") == name_key("Anna Meier") == "anna meier"

    def test_a_value_without_a_comma_is_returned_unchanged(self):
        assert reverse_comma_name("Anna Meier") == "Anna Meier"

    def test_the_sharp_s_folds_to_ss(self):
        assert normalize_name("Straße") == "strasse"
        assert normalize_name("Straße") == normalize_name("Strasse")

    def test_accents_are_stripped_but_not_transliterated(self):
        assert normalize_name("Anna Müller") == "anna muller"
        assert normalize_name("Anna Mueller") == "anna mueller"
        assert normalize_name("Anna Müller") != normalize_name("Anna Mueller")

    def test_deslugify_spells_a_slug_back_out(self):
        assert deslugify("anna-meier") == "Anna Meier"

    def test_email_addresses_are_found_inside_prose(self):
        assert email_addresses("write to Anna <Anna@Example.com> today") == ["anna@example.com"]

    @given(st.lists(LATIN, min_size=1, max_size=4))
    def test_shuffling_the_words_never_changes_the_key(self, words):
        assert normalize_name(" ".join(words)) == normalize_name(" ".join(reversed(words)))

    @given(st.lists(LATIN, min_size=1, max_size=4))
    def test_the_key_is_its_own_key(self, words):
        once = normalize_name(" ".join(words))
        assert normalize_name(once) == once


# ---------------------------------------------------------------------------
# links
# ---------------------------------------------------------------------------


LINKS = LinkResolutionConfig(unresolved_prefix="contact-", target_type="person")


class TestResolveLinks:
    @pytest.fixture()
    def corpus(self, backend):
        return loaded(
            backend,
            [
                entity("person-anna", "Anna Meier", "person", [observation("email", "anna@example.com")]),
                entity("person-bo", "Bo, Ravi", "person"),
                entity("person-cleo", "Cleo Nix", "person"),
                entity(
                    "note-one",
                    "Note one",
                    "note",
                    relations=[
                        relation("links_to", "contact-anna-meier"),
                        relation("links_to", "contact-ravi-bo"),
                        relation("links_to", "contact-anna@example.com"),
                        relation("links_to", "contact-nobody-at-all"),
                        relation("links_to", "some-other-link"),
                    ],
                ),
            ],
        )

    def verdicts(self, corpus):
        return {
            resolution.link_text: (
                resolution.confidence,
                None if resolution.matched is None else resolution.matched.permalink,
            )
            for resolution in resolve_links(corpus, LINKS, PAGE_SIZE)
        }

    def test_an_exact_title_is_high_confidence(self, corpus):
        assert self.verdicts(corpus)["contact-anna-meier"] == ("high", "person-anna")

    def test_an_email_address_is_high_confidence(self, corpus):
        assert self.verdicts(corpus)["contact-anna@example.com"] == ("high", "person-anna")

    def test_a_match_that_needed_normalising_is_medium_confidence(self, corpus):
        assert self.verdicts(corpus)["contact-ravi-bo"] == ("medium", "person-bo")

    def test_no_candidate_is_unresolved(self, corpus):
        assert self.verdicts(corpus)["contact-nobody-at-all"] == ("unresolved", None)

    def test_a_link_without_the_configured_prefix_is_not_reported(self, corpus):
        assert "some-other-link" not in self.verdicts(corpus)

    def test_an_already_resolved_link_is_not_reported(self, backend):
        corpus = loaded(
            backend,
            [
                entity("person-anna", "Anna Meier", "person"),
                entity("note-one", "Note one", "note", relations=[relation("links_to", "Anna Meier")]),
            ],
        )
        corpus.resolve_relations()
        assert resolve_links(corpus, LINKS, PAGE_SIZE) == []

    def test_the_prefix_is_config_not_code(self, backend):
        corpus = loaded(
            backend,
            [
                entity("person-anna", "Anna Meier", "person"),
                entity("note-one", "Note one", "note", relations=[relation("who-is-anna-meier", "who-is-anna-meier")]),
            ],
        )
        renamed = LinkResolutionConfig(unresolved_prefix="who-is-", target_type="person")
        assert [r.confidence for r in resolve_links(corpus, renamed, PAGE_SIZE)] == ["high"]


# ---------------------------------------------------------------------------
# tiers
# ---------------------------------------------------------------------------


EMAIL_SOURCE = observation_source("email", "from", "received_at", exclude_future=True)


def messages_from(store: InMemoryIndexBackend, party: str, days: list[int]):
    return loaded(
        store,
        [
            entity(
                f"email-{party}-{day}",
                f"Message {day}",
                "email",
                [observation("from", party), observation("received_at", f"2026-07-{day:02d}T09:00:00Z")],
            )
            for day in days
        ],
    )


class TestLadder:
    @pytest.mark.parametrize(
        ("rungs", "rate", "expected"),
        [
            (ladder(("close", 4.0, 14), ("rest", 0.0, None)), 5.0, "close"),
            (ladder(("close", 4.0, 14), ("rest", 0.0, None)), 0.5, "rest"),
            (ladder(("a", 8.0, 7), ("b", 4.0, 14), ("c", 0.0, None)), 4.0, "b"),
            (
                ladder(("a", 16.0, 3), ("b", 8.0, 7), ("c", 4.0, 14), ("d", 1.0, 30), ("e", 0.0, None)),
                1.5,
                "d",
            ),
        ],
    )
    def test_the_same_code_path_serves_two_three_and_five_rungs(self, rungs, rate, expected):
        chosen = assign_rung(rungs, rate)
        assert chosen is not None
        assert chosen.name == expected

    def test_a_rate_below_every_rung_is_in_no_tier(self):
        assert assign_rung(ladder(("close", 4.0, 14), ("regular", 1.0, 30)), 0.5) is None

    def test_a_terminal_rung_never_goes_stale(self):
        terminal = ladder(("rest", 0.0, None))[0]
        assert is_stale(terminal, datetime(2020, 1, 1, tzinfo=UTC), NOW) is False

    def test_a_rung_with_a_tolerance_goes_stale_past_it(self):
        rung = ladder(("close", 4.0, 14))[0]
        assert is_stale(rung, NOW.replace(day=10, month=7), NOW) is True
        assert is_stale(rung, NOW.replace(day=25, month=7), NOW) is False

    @given(
        st.lists(st.floats(min_value=0.0, max_value=50.0, allow_nan=False), min_size=1, max_size=6),
        st.floats(min_value=0.0, max_value=50.0, allow_nan=False),
    )
    def test_the_chosen_rung_is_the_first_the_rate_meets(self, minimums, rate):
        rungs = ladder(*((f"rung-{position}", minimum, None) for position, minimum in enumerate(minimums)))
        chosen = assign_rung(rungs, rate)
        if chosen is None:
            assert all(rate < rung.min_per_month for rung in rungs)
            return
        position = [rung.name for rung in rungs].index(chosen.name)
        assert rate >= chosen.min_per_month
        assert all(rate < earlier.min_per_month for earlier in rungs[:position])


class TestComputeTiers:
    def test_a_frequent_counterparty_lands_on_the_top_rung(self, backend):
        corpus = messages_from(backend, "anna@example.com", [1, 5, 9, 13, 17, 21])
        # six interactions over a 45-day window is exactly 4.0 a month, the top rung's minimum.
        config = tiers_config(ladder(("close", 4.0, 14), ("rest", 0.0, None)), [EMAIL_SOURCE], lookback_days=45)
        assignments = compute_tiers(corpus, config, NOW, PAGE_SIZE)
        assert [(a.party, a.tier, a.interactions) for a in assignments] == [("anna@example.com", "close", 6)]

    def test_moving_the_threshold_is_a_config_change_and_nothing_else(self, backend):
        corpus = messages_from(backend, "anna@example.com", [1, 5, 9, 13, 17, 21])
        sources = [EMAIL_SOURCE]
        generous = tiers_config(ladder(("close", 1.0, 14), ("rest", 0.0, None)), sources, lookback_days=45)
        strict = tiers_config(ladder(("close", 9.0, 14), ("rest", 0.0, None)), sources, lookback_days=45)
        assert compute_tiers(corpus, generous, NOW, PAGE_SIZE)[0].tier == "close"
        assert compute_tiers(corpus, strict, NOW, PAGE_SIZE)[0].tier == "rest"

    def test_two_spellings_of_one_name_are_one_counterparty(self, backend):
        corpus = loaded(
            backend,
            [
                entity(
                    "email-a",
                    "A",
                    "email",
                    [observation("from", "Meier, Anna"), observation("received_at", "2026-07-20T09:00:00Z")],
                ),
                entity(
                    "email-b",
                    "B",
                    "email",
                    [observation("from", "anna meier"), observation("received_at", "2026-07-21T09:00:00Z")],
                ),
            ],
        )
        config = tiers_config(ladder(("rest", 0.0, None)), [EMAIL_SOURCE], lookback_days=90)
        assignments = compute_tiers(corpus, config, NOW, PAGE_SIZE)
        assert [(a.key, a.interactions) for a in assignments] == [("anna meier", 2)]

    def test_an_interaction_outside_the_lookback_window_is_not_counted(self, backend):
        corpus = messages_from(backend, "anna@example.com", [1, 20])
        config = tiers_config(ladder(("rest", 0.0, None)), [EMAIL_SOURCE], lookback_days=15)
        assert compute_tiers(corpus, config, NOW, PAGE_SIZE)[0].interactions == 1

    def test_a_future_timestamp_is_dropped_when_the_source_says_so(self, backend):
        corpus = loaded(
            backend,
            [
                entity(
                    "email-future",
                    "Later",
                    "email",
                    [observation("from", "anna@example.com"), observation("received_at", "2027-01-01T09:00:00Z")],
                ),
                entity(
                    "email-past",
                    "Earlier",
                    "email",
                    [observation("from", "anna@example.com"), observation("received_at", "2026-07-20T09:00:00Z")],
                ),
            ],
        )
        config = tiers_config(ladder(("rest", 0.0, None)), [EMAIL_SOURCE], lookback_days=90)
        assert compute_tiers(corpus, config, NOW, PAGE_SIZE)[0].interactions == 1

    def test_a_counterparty_can_come_from_a_relation_instead_of_an_observation(self, backend):
        corpus = loaded(
            backend,
            [
                entity(
                    "event-one",
                    "Review",
                    "event",
                    [observation("start_time", "2026-07-20T09:00:00Z")],
                    [relation("attended_by", "Anna Meier")],
                ),
            ],
        )
        source = InteractionSourceConfig(
            entity_type="event",
            party_from=PartySelector(observation=None, relation="attended_by"),
            timestamp=TimestampSelector(observation="start_time"),
            exclude_future=True,
        )
        config = tiers_config(ladder(("rest", 0.0, None)), [source], lookback_days=90)
        assert [a.party for a in compute_tiers(corpus, config, NOW, PAGE_SIZE)] == ["Anna Meier"]

    def test_write_back_is_refused_rather_than_ignored(self, backend):
        config = TiersConfig(
            lookback_days=90,
            ladder=ladder(("rest", 0.0, None)),
            interaction_sources=[EMAIL_SOURCE],
            write_back=TierWriteBackConfig(enabled=True, fields={"tier": "tier"}, create_missing=False),
        )
        with pytest.raises(ConfigError, match="write_back"):
            compute_tiers(backend, config, NOW, PAGE_SIZE)

    def test_a_non_iso_timestamp_names_the_entity_it_came_from(self, backend):
        corpus = loaded(
            backend,
            [entity("email-bad", "Bad", "email", [observation("from", "a@b.com"), observation("received_at", "soon")])],
        )
        config = tiers_config(ladder(("rest", 0.0, None)), [EMAIL_SOURCE], lookback_days=90)
        with pytest.raises(ValueError, match="email-bad"):
            compute_tiers(corpus, config, NOW, PAGE_SIZE)


# ---------------------------------------------------------------------------
# triage
# ---------------------------------------------------------------------------


TRIAGE = TriageConfig(
    own_email="owner@example.com",
    inbox_folder="Inbox",
    sent_folders=["SentItems"],
    forward_prefixes=["fw:", "fwd:"],
)

FIELDS = MessageFields(
    entity_type="email",
    folder="folder",
    conversation_id="conversation",
    sender="sender",
    recipients="to",
    timestamp="date",
)


def message(key: str, subject: str, folder: str, conversation: str, when: str, to: str) -> Entity:
    return entity(
        key,
        subject,
        "email",
        [
            observation("folder", folder),
            observation("conversation", conversation),
            observation("sender", "alice@example.com"),
            observation("to", to),
            observation("date", when),
        ],
    )


def rejected_reply(store: InMemoryIntentStore, uuid: str, in_reply_to: str) -> None:
    """Dispatch a reply, then record the verdict a deleted draft produces."""
    envelope = IntentEnvelope(
        uuid=uuid,
        schema_version=1,
        created_at=datetime(2026, 7, 1, tzinfo=UTC),
        created_by="test",
        payload=EmailReplyPayload(
            kind="email.reply",
            mailbox="me",
            body="draft",
            attachments=None,
            inline_images=None,
            include_signature=True,
            revises_message_id=None,
            in_reply_to=in_reply_to,
            reply_all=False,
            cc=None,
        ),
    )
    store.put("email.reply", uuid, dump_intent(envelope))
    store.claim("email.reply", uuid)
    store.archive(
        uuid,
        DispatchReceipt(
            uuid=uuid,
            kind="email.reply",
            outcome="dispatched",
            dispatched_at=datetime(2026, 7, 1, tzinfo=UTC),
            graph_message_id="graph-id",
            reason=None,
            detail=None,
        ),
    )
    store.mark_reconciled(uuid, "rejected")


class TestTriagePredicates:
    def test_is_forward_follows_the_configured_prefixes(self):
        assert is_forward("FW: budget", TRIAGE.forward_prefixes) is True
        assert is_forward("Fwd: budget", TRIAGE.forward_prefixes) is True
        assert is_forward("budget", TRIAGE.forward_prefixes) is False
        assert is_forward("WG: budget", TRIAGE.forward_prefixes) is False
        assert is_forward("WG: budget", ["wg:"]) is True

    def test_is_cc_only_asks_whether_the_owner_was_on_the_to_line(self):
        assert is_cc_only(frozenset({"owner@example.com"}), TRIAGE.own_email) is False
        assert is_cc_only(frozenset({"bob@example.com"}), TRIAGE.own_email) is True


class TestTriage:
    @pytest.fixture()
    def corpus(self, backend):
        return loaded(
            backend,
            [
                message("m-open", "Question", "Inbox", "conv-1", "2026-07-30T09:00:00Z", "owner@example.com"),
                message("m-answered", "Reply me", "Inbox", "conv-2", "2026-07-30T09:00:00Z", "owner@example.com"),
                message("m-sent", "Re: Reply me", "SentItems", "conv-2", "2026-07-31T09:00:00Z", "alice@example.com"),
                message("m-declined", "Ignore me", "Inbox", "conv-3", "2026-07-30T09:00:00Z", "owner@example.com"),
                message("m-old", "Ancient", "Inbox", "conv-4", "2026-06-01T09:00:00Z", "owner@example.com"),
                message("m-fwd", "FW: notes", "Inbox", "conv-5", "2026-07-30T09:00:00Z", "bob@example.com"),
            ],
        )

    @pytest.fixture()
    def store(self):
        store = InMemoryIntentStore()
        rejected_reply(store, "intent-one", "conv-3")
        return store

    def keys(self, corpus, store):
        return [item.entity.permalink for item in triage(corpus, store, TRIAGE, FIELDS, "7d", NOW, PAGE_SIZE)]

    def test_a_message_with_no_reply_is_reported(self, corpus, store):
        assert "m-open" in self.keys(corpus, store)

    def test_a_message_with_a_sent_sibling_in_its_conversation_is_excluded(self, corpus, store):
        assert "m-answered" not in self.keys(corpus, store)

    def test_a_message_a_rejected_intent_pointed_at_is_excluded(self, corpus, store):
        assert "m-declined" not in self.keys(corpus, store)

    def test_a_message_outside_the_timeframe_is_excluded(self, corpus, store):
        assert "m-old" not in self.keys(corpus, store)

    def test_a_sent_message_is_never_itself_reported(self, corpus, store):
        assert "m-sent" not in self.keys(corpus, store)

    def test_the_forward_and_cc_flags_come_from_the_message(self, corpus, store):
        items = {i.entity.permalink: i for i in triage(corpus, store, TRIAGE, FIELDS, "7d", NOW, PAGE_SIZE)}
        assert (items["m-fwd"].is_forward, items["m-fwd"].is_cc_only) == (True, True)
        assert (items["m-open"].is_forward, items["m-open"].is_cc_only) == (False, False)

    def test_a_dispatched_intent_with_no_rejection_declines_nothing(self, corpus):
        store = InMemoryIntentStore()
        rejected_reply(store, "intent-one", "conv-3")
        store.mark_reconciled("intent-one", "sent")
        assert "m-declined" in self.keys(corpus, store)

    def test_rejected_references_names_the_message_the_draft_answered(self):
        store = InMemoryIntentStore()
        rejected_reply(store, "intent-one", "conv-3")
        assert rejected_references(store) == frozenset({"conv-3"})

    def test_a_message_missing_a_required_observation_is_named_not_skipped(self, backend):
        corpus = loaded(backend, [entity("m-broken", "Broken", "email", [observation("folder", "Inbox")])])
        with pytest.raises(ValueError, match="m-broken"):
            triage(corpus, InMemoryIntentStore(), TRIAGE, FIELDS, "7d", NOW, PAGE_SIZE)
