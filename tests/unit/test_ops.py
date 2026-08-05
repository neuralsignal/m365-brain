"""The three operational reports, and the ladder that has no special case.

The tests that matter most here are the ones that would pass trivially if the
ported scripts had been copied instead of rewritten: a ladder of two, three and
five rungs going through the same code path, and a threshold moving purely in
config.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
import yaml
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from m365_brain.commands.ops import _fields, triage_command
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
    TriageFieldsConfig,
)
from m365_brain.index.backends.memory import InMemoryIndexBackend
from m365_brain.m365.frontmatter.calendar import CalendarEventData, attendee_relations, build_calendar_frontmatter
from m365_brain.m365.frontmatter.email import EmailData, build_email_frontmatter
from m365_brain.m365.frontmatter.people import (
    ContactData,
    DirectoryUserData,
    address_observations,
    build_contact_frontmatter,
    build_directory_user_frontmatter,
)
from m365_brain.m365.frontmatter.teams import TeamsChatData, build_teams_chat_frontmatter, participant_relations
from m365_brain.m365.markdown_writer import dumps_markdown
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
from m365_brain.ops.triage import is_cc_only, is_forward, rejected_references, triage
from m365_brain.outbox.stores import InMemoryIntentStore
from m365_brain.parsers.document import parse_markdown_file
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


LINKS = LinkResolutionConfig(unresolved_prefix="contact-", target_types=["person"])
"""A target type that is deliberately *not* one the bundled builders write.

Same reason `FIELDS` below spells its own categories: these tests state the
behaviour of the verb, and a fixture that happened to agree with the shipped
template would pass while the template disagreed with the extractors -- the
shape both halves of this defect had. The pairing is asserted once, at the
bottom of the file, against the real builders' output.
"""


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
        renamed = LinkResolutionConfig(unresolved_prefix="who-is-", target_types=["person"])
        assert [r.confidence for r in resolve_links(corpus, renamed, PAGE_SIZE)] == ["high"]

    def test_a_second_target_type_is_a_candidate_like_the_first(self, backend):
        """One corpus spells a person two ways, and both are lookups.

        A single-name key would have made this an operator's choice between two
        halves of their own address book.
        """
        corpus = loaded(
            backend,
            [
                entity("person-anna", "Anna Meier", "contact"),
                entity("person-bo", "Bo Frey", "directory_user"),
                entity(
                    "note-one",
                    "Note one",
                    "note",
                    relations=[relation("links_to", "contact-anna-meier"), relation("links_to", "contact-bo-frey")],
                ),
            ],
        )
        both = LinkResolutionConfig(unresolved_prefix="contact-", target_types=["contact", "directory_user"])

        assert [(r.confidence, r.matched.permalink) for r in resolve_links(corpus, both, PAGE_SIZE)] == [
            ("high", "person-anna"),
            ("high", "person-bo"),
        ]

    def test_naming_no_target_type_is_refused_rather_than_reported_as_empty(self):
        """An all-unresolved report is indistinguishable from a corpus with nobody in it."""
        with pytest.raises(ValidationError, match="target_types"):
            LinkResolutionConfig(unresolved_prefix="contact-", target_types=[])


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


FIELDS = TriageFieldsConfig(
    entity_type="email",
    folder="folder",
    conversation_id="conversation",
    message_id="graph_id",
    sender="sender",
    recipients="to",
    timestamp="date",
)
"""A vocabulary that is deliberately *not* the bundled extractor's.

`conversation` rather than `conversation_id`, `graph_id` rather than
`message_id`, so these tests cannot pass by accidentally agreeing with the
shipped config. The pairing tests at the bottom of the file are the ones that
read what the extractor actually writes.
"""

TRIAGE = TriageConfig(
    own_email="owner@example.com",
    inbox_folder="Inbox",
    sent_folders=["SentItems"],
    forward_prefixes=["fw:", "fwd:"],
    fields=FIELDS,
)


def message(key: str, subject: str, folder: str, conversation: str, when: str, to: str) -> Entity:
    """One indexed message. Its message id is its key -- a *different* identifier
    from `conversation`, which is the distinction the declined clause turns on."""
    return entity(
        key,
        subject,
        "email",
        [
            observation("folder", folder),
            observation("conversation", conversation),
            observation("graph_id", key),
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
        rejected_reply(store, "intent-one", "m-declined")
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
        rejected_reply(store, "intent-one", "m-declined")
        store.mark_reconciled("intent-one", "sent")
        assert "m-declined" in self.keys(corpus, store)

    def test_a_rejection_naming_the_conversation_declines_nothing(self, corpus):
        """`in_reply_to` is a message id; a thread id is a different space.

        The clause used to compare it against the conversation, so it matched
        only where a test happened to feed a conversation id in -- and never on
        a corpus, where the two identifiers never coincide.
        """
        store = InMemoryIntentStore()
        rejected_reply(store, "intent-one", "conv-3")
        assert "m-declined" in self.keys(corpus, store)

    def test_rejected_references_names_the_message_the_draft_answered(self):
        store = InMemoryIntentStore()
        rejected_reply(store, "intent-one", "m-declined")
        assert rejected_references(store) == frozenset({"m-declined"})

    def test_a_message_missing_a_required_observation_is_named_not_skipped(self, backend):
        corpus = loaded(backend, [entity("m-broken", "Broken", "email", [observation("folder", "Inbox")])])
        with pytest.raises(ValueError, match="m-broken"):
            triage(corpus, InMemoryIntentStore(), TRIAGE, FIELDS, "7d", NOW, PAGE_SIZE)

    def test_a_blank_conversation_id_is_refused_rather_than_grouped(self, backend):
        """An empty thread id is worse than a missing one: it collides.

        Every message carrying `""` lands in one thread, so a single sent mail
        with a blank id would answer all of them at once -- and the report would
        be empty rather than wrong-looking.
        """
        corpus = loaded(
            backend,
            [
                entity(
                    "m-blank",
                    "Blank thread",
                    "email",
                    [
                        observation("folder", "Inbox"),
                        observation("conversation", "   "),
                        observation("date", "2026-07-30T09:00:00Z"),
                    ],
                )
            ],
        )
        with pytest.raises(ValueError, match="m-blank"):
            triage(corpus, InMemoryIntentStore(), TRIAGE, FIELDS, "7d", NOW, PAGE_SIZE)


# ---------------------------------------------------------------------------
# the shipped config, read against what the bundled builders actually write
# ---------------------------------------------------------------------------


TEMPLATE = Path(__file__).resolve().parents[2] / "m365_brain" / "templates" / "m365-brain.yaml"

SHIPPED = yaml.safe_load(TEMPLATE.read_text(encoding="utf-8"))

SHIPPED_TRIAGE = TriageConfig.model_validate(SHIPPED["ops"]["triage"] | {"own_email": "owner@example.com"})
"""The shipped `ops.triage`, with only the `${VAR}` field replaced.

Loading it rather than restating it is the point: a test that spelled the seven
categories itself would agree with itself while the template disagreed with the
extractor, which is the shape the original defect had.
"""

SHIPPED_TIERS = TiersConfig.model_validate(SHIPPED["ops"]["tiers"])
"""The shipped `ops.tiers`, loaded for the same reason and with nothing replaced."""


def received(subject: str, conversation_id: str, folder: str, when: str, to: list[str]) -> EmailData:
    return EmailData(
        subject=subject,
        message_id=f"msg-{subject}",
        conversation_id=conversation_id,
        received_time=when,
        folder=folder,
        mailbox="owner@example.com",
        sender_address="alice@example.com",
        sender_name="Alice",
        to_recipients=to,
        importance="normal",
        has_attachments=False,
        web_link="",
    )


class TestTriageOverExtractorOutput:
    """The pairing rule, end to end over the email builder's own frontmatter.

    Every other triage test hands the index observations it wrote by hand, so
    all of them passed while the extractor emitted no conversation id at all and
    `ops triage` could not run on a real corpus. This one starts at
    `build_email_frontmatter`, goes through the real markdown parser into the
    real index, and reads the categories the shipped config names -- so it fails
    if either side of that agreement moves.
    """

    @pytest.fixture()
    def corpus(self, backend, corpus_root, index_config):
        messages = [
            received("Budget question", "conv-unanswered", "Inbox", "2026-07-30T09:00:00Z", ["owner@example.com"]),
            received("Invoice query", "conv-answered", "Inbox", "2026-07-30T09:00:00Z", ["owner@example.com"]),
            received("Re: Invoice query", "conv-answered", "SentItems", "2026-07-31T09:00:00Z", ["alice@example.com"]),
        ]
        entities = []
        for data in messages:
            frontmatter = build_email_frontmatter(data)
            path = corpus_root / f"{frontmatter['permalink']}.md"
            path.write_text(dumps_markdown(frontmatter, f"# {data.subject}\n"), encoding="utf-8")
            parsed = parse_markdown_file(path, index_config.roots[0], index_config)
            assert parsed is not None
            entities.append(parsed)
        return loaded(backend, entities)

    def items(self, corpus):
        return triage(corpus, InMemoryIntentStore(), SHIPPED_TRIAGE, SHIPPED_TRIAGE.fields, "7d", NOW, PAGE_SIZE)

    def test_exactly_the_message_with_no_reply_is_reported(self, corpus):
        """One of the two received messages has a sent sibling; only the other survives."""
        assert [item.subject for item in self.items(corpus)] == ["Budget question"]

    def test_the_reported_message_carries_the_thread_it_was_paired_on(self, corpus):
        assert [item.conversation_id for item in self.items(corpus)] == ["conv-unanswered"]

    def test_the_owner_is_seen_on_the_joined_to_line(self, corpus):
        """`to` is one joined string by the time it reaches the index, and still parses."""
        assert [item.is_cc_only for item in self.items(corpus)] == [False]

    def test_a_rejected_draft_excludes_the_message_it_answered(self, corpus):
        """`in_reply_to` is a Graph message id and is matched as one, end to end."""
        store = InMemoryIntentStore()
        rejected_reply(store, "intent-one", "msg-Budget question")

        assert triage(corpus, store, SHIPPED_TRIAGE, SHIPPED_TRIAGE.fields, "7d", NOW, PAGE_SIZE) == []

    def test_the_shipped_categories_are_the_ones_the_builder_writes(self):
        """The config-side half of the same agreement, stated once, in one place."""
        frontmatter = build_email_frontmatter(
            received("Anything", "conv-1", "Inbox", "2026-07-30T09:00:00Z", ["owner@example.com"])
        )
        fields = SHIPPED_TRIAGE.fields
        named = set(fields.model_dump().values()) - {fields.entity_type}

        assert frontmatter["type"] == fields.entity_type
        assert named <= set(frontmatter)
        # A structure is metadata and unreadable per entity, so naming one is
        # the same defect as naming a key that does not exist.
        assert all(not isinstance(frontmatter[category], dict | list) for category in named)


class TestTriageCommandFields:
    """`ops triage` reads its categories from config; the options only override.

    The verb previously demanded all seven on every invocation, which is the same
    defect as a code default wearing the opposite disguise: the value was config,
    and the operator retyped it.
    """

    CATEGORY_OPTIONS = (
        "entity_type",
        "folder_category",
        "conversation_category",
        "message_id_category",
        "sender_category",
        "recipients_category",
        "timestamp_category",
    )

    def test_no_category_option_is_required(self):
        """One assert covering both halves: the seven exist, and none demands a value."""
        stated = {
            parameter.name: parameter.required
            for parameter in triage_command.params
            if parameter.name in self.CATEGORY_OPTIONS
        }

        assert stated == dict.fromkeys(self.CATEGORY_OPTIONS, False)

    def test_stating_nothing_uses_the_configured_names(self):
        assert _fields(FIELDS, dict.fromkeys(FIELDS.model_dump())) == FIELDS

    def test_an_override_replaces_one_name_and_leaves_the_rest(self):
        overridden = _fields(FIELDS, {"conversation_id": "thread", "folder": None})

        assert overridden.conversation_id == "thread"
        assert overridden.model_dump() | {"conversation_id": FIELDS.conversation_id} == FIELDS.model_dump()


# ---------------------------------------------------------------------------
# every shipped interaction source, against the producer it names
# ---------------------------------------------------------------------------


WHEN = "2026-07-20T09:00:00Z"
"""Inside the shipped lookback window and behind `NOW`, so `exclude_future` holds."""


def _email_document() -> tuple[str, str]:
    """`(markdown the email builder produces, the counterparty it states)`."""
    data = received("Budget question", "conv-1", "Inbox", WHEN, ["owner@example.com"])
    return dumps_markdown(build_email_frontmatter(data), f"# {data.subject}\n"), data.sender_address


def _event_document() -> tuple[str, str]:
    """The same for the calendar builder -- frontmatter *and* attendee relations.

    Both halves, because the counterparty is not in the frontmatter at all: an
    event has N of them and only the body can carry N readable values.
    """
    data = CalendarEventData(
        subject="Weekly review",
        event_id="evt-1",
        start_time=WHEN,
        end_time=WHEN,
        location="",
        organizer_name="Owner",
        organizer_email="owner@example.com",
        attendees=["Robin Vale"],
        attendee_details=[{"name": "Robin Vale", "email": "robin@example.com", "status": "accepted"}],
        is_recurring=False,
        web_link="",
    )
    body = "\n".join([f"# {data.subject}\n", *attendee_relations(data)])
    return dumps_markdown(build_calendar_frontmatter(data), body), "Robin Vale"


def _chat_document() -> tuple[str, str]:
    """The same for the Teams chat builder -- frontmatter *and* participant relations.

    `participants` is a list, so like an event's attendees the counterparty is
    not in the frontmatter at all.
    """
    data = TeamsChatData(
        title="Project sync",
        conversation_id="chat-1",
        conversation_type="group",
        participants=["Sam Okoro"],
        last_message_time=WHEN,
        message_count=3,
        history_complete=True,
    )
    body = "\n".join([f"# {data.title}\n", *participant_relations(data)])
    return dumps_markdown(build_teams_chat_frontmatter(data), body), "Sam Okoro"


PRODUCERS = {"email": _email_document, "calendar_event": _event_document, "teams_chat": _chat_document}
"""`entity_type` -> the bundled producer that writes it.

Keyed by the entity type so the parametrised test below can look a source up by
the name the shipped config uses. A source naming a type absent from this table
fails rather than skips: "no producer writes this" is exactly the defect.
"""


def indexed(store: InMemoryIndexBackend, corpus_root: Path, index_config, documents: list[tuple[str, str]]):
    """Write markdown, parse it with the real parser, index it. No hand-built entities."""
    entities = []
    for name, text in documents:
        path = corpus_root / f"{name}.md"
        path.write_text(text, encoding="utf-8")
        parsed = parse_markdown_file(path, index_config.roots[0], index_config)
        assert parsed is not None
        entities.append(parsed)
    return loaded(store, entities)


class TestTiersOverExtractorOutput:
    """`ops tiers` over the shipped config and the bundled builders' real output.

    Every other tiers test writes the observations it then reads, so all of them
    passed while both shipped sources named categories no builder wrote and the
    verb reported zero counterparties against a full vault -- which reads as a
    quiet quarter, not as a bug.
    """

    @pytest.fixture()
    def corpus(self, backend, corpus_root, index_config):
        return indexed(
            backend,
            corpus_root,
            index_config,
            [(entity_type, producer()[0]) for entity_type, producer in sorted(PRODUCERS.items())],
        )

    def test_the_shipped_config_reports_the_counterparties_it_was_given(self, corpus):
        """The regression itself: an empty report over a corpus this library wrote."""
        assignments = compute_tiers(corpus, SHIPPED_TIERS, NOW, PAGE_SIZE)

        # Ordered by `names.name_key`, which word-sorts: `okoro sam` before `robin vale`.
        assert [(a.party, a.interactions) for a in assignments] == [
            ("alice@example.com", 1),
            ("Sam Okoro", 1),
            ("Robin Vale", 1),
        ]

    @pytest.mark.parametrize(
        "source", SHIPPED_TIERS.interaction_sources, ids=[s.entity_type for s in SHIPPED_TIERS.interaction_sources]
    )
    def test_every_shipped_source_reads_the_builder_it_names(
        self, backend, corpus_root, index_config, source: InteractionSourceConfig
    ):
        """The generalised guard: one source at a time, whatever the template lists.

        Driving the source rather than asserting on key names covers the shape
        rule too -- a list-valued frontmatter key is metadata, so a source
        naming one yields nothing here without the test having to restate why.
        """
        assert source.entity_type in PRODUCERS, f"no bundled producer writes {source.entity_type!r}"
        text, party = PRODUCERS[source.entity_type]()
        corpus = indexed(backend, corpus_root, index_config, [(source.entity_type, text)])
        config = tiers_config(SHIPPED_TIERS.ladder, [source], SHIPPED_TIERS.lookback_days)

        assert [(a.party, a.interactions) for a in compute_tiers(corpus, config, NOW, PAGE_SIZE)] == [(party, 1)]


# ---------------------------------------------------------------------------
# every shipped link-resolution target type, against the producer it names
# ---------------------------------------------------------------------------


def _contact_document() -> tuple[str, str, str]:
    """`(markdown the contact builder produces, its permalink, an address it states)`.

    Frontmatter *and* address observations, because the address is not readable
    from the frontmatter at all: a contact has N of them, so `email` is a list,
    and a list stays in metadata.
    """
    data = ContactData(
        display_name="Kai Lund",
        contact_id="contact-1",
        email_addresses=["kai@example.com"],
        phones=[],
        company="",
        job_title="",
        department="",
        categories=[],
    )
    frontmatter = build_contact_frontmatter(data)
    body = "\n".join([f"# {data.display_name}\n", "## Details\n", *address_observations(data)])
    return dumps_markdown(frontmatter, body), frontmatter["permalink"], data.email_addresses[0]


def _directory_user_document() -> tuple[str, str, str]:
    """The same for the directory builder, whose `email` is a scalar and needs no body line."""
    data = DirectoryUserData(
        display_name="Mira Sund",
        user_id="user-1",
        email="mira@example.com",
        upn="mira@example.com",
        job_title="",
        department="",
        office="",
        city="",
        manager_link="",
        direct_reports_links=[],
    )
    frontmatter = build_directory_user_frontmatter(data)
    return dumps_markdown(frontmatter, f"# {data.display_name}\n"), frontmatter["permalink"], data.email


PEOPLE_PRODUCERS = {"contact": _contact_document, "directory_user": _directory_user_document}
"""`entity_type` -> the bundled producer that writes a person under it.

The link-resolution counterpart of `PRODUCERS`, and read the same way: a target
type absent from this table fails rather than skips, because "no producer writes
this" is the defect, not a gap in the fixtures.
"""

SHIPPED_LINKS = LinkResolutionConfig.model_validate(SHIPPED["ops"]["link_resolution"])
"""The shipped `ops.link_resolution`, loaded rather than restated -- see `SHIPPED_TIERS`."""


def _note_linking(targets: list[str]) -> str:
    """A note whose only content is one dangling link per target."""
    frontmatter = {"title": "Meeting notes", "permalink": "note-links", "type": "note", "tags": []}
    lines = [f"- mentions [[{SHIPPED_LINKS.unresolved_prefix}{target}]]" for target in targets]
    return dumps_markdown(frontmatter, "\n".join(["# Meeting notes\n", *lines]))


class TestResolveLinksOverExtractorOutput:
    """`ops links` over the shipped config and the bundled people builders' real output.

    Every other test in `TestResolveLinks` hands the index entities it wrote by
    hand, so all of them passed while the shipped `target_type` named `person` --
    a type no bundled builder writes -- and the contact's address reached neither
    an observation nor a relation. Against a corpus this library produced, the
    `high`-confidence address match could not be returned at all, and an
    all-unresolved report reads as a corpus with nobody in it.

    `resolve_relations` is run before every assertion, so the links really are
    ones the index could not resolve on its own rather than ones nobody asked it
    to.
    """

    def corpus(self, backend, corpus_root, index_config, entity_types: list[str]):
        produced = [PEOPLE_PRODUCERS[entity_type]() for entity_type in entity_types]
        documents = [(entity_type, text) for entity_type, (text, _, _) in zip(entity_types, produced, strict=True)]
        documents.append(("note-links", _note_linking([address for _, _, address in produced])))
        store = indexed(backend, corpus_root, index_config, documents)
        store.resolve_relations()
        return store, produced

    def test_the_shipped_config_resolves_every_person_it_was_given(self, backend, corpus_root, index_config):
        """The regression itself: an empty report over a corpus this library wrote."""
        store, produced = self.corpus(backend, corpus_root, index_config, sorted(PEOPLE_PRODUCERS))
        resolutions = resolve_links(store, SHIPPED_LINKS, PAGE_SIZE)

        assert [(r.confidence, r.matched.permalink) for r in resolutions] == [
            ("high", permalink) for _, permalink, _ in produced
        ]

    @pytest.mark.parametrize("target_type", SHIPPED_LINKS.target_types)
    def test_every_shipped_target_type_is_written_by_the_builder_it_names(
        self, backend, corpus_root, index_config, target_type: str
    ):
        """The generalised guard: one target type at a time, whatever the template lists.

        Driving the type rather than asserting on names covers the shape rule
        too -- an address that never reaches an observation is unreadable, so a
        type whose builder writes one into a list yields `unresolved` here
        without the test having to restate why.
        """
        assert target_type in PEOPLE_PRODUCERS, f"no bundled producer writes {target_type!r}"
        store, [(_, permalink, address)] = self.corpus(backend, corpus_root, index_config, [target_type])

        assert [
            (r.link_text, r.confidence, r.matched.permalink) for r in resolve_links(store, SHIPPED_LINKS, PAGE_SIZE)
        ] == [(f"{SHIPPED_LINKS.unresolved_prefix}{address}", "high", permalink)]

    def test_the_unresolved_prefix_is_the_one_a_person_permalink_starts_with(self):
        """The prefix half of the same agreement, and the reason `contact-` is it.

        Nothing in this library writes such a link -- the Teams extractor was the
        last to, and stopped. The prefix marks a link a *human* wrote by half
        remembering the permalink shape, so the shipped value is the one the
        bundled contact builder's permalinks actually begin with.
        """
        _, permalink, _ = _contact_document()

        assert permalink.startswith(SHIPPED_LINKS.unresolved_prefix)


class TestShippedFolderNames:
    """The folder half of the same family, checkable without a corpus.

    The email extractor writes `folder` verbatim from the mailbox's configured
    folder list, so a triage filter naming a folder that list never produces
    reports nothing -- the same silence, from the other direction.
    """

    EXTRACTED = {
        folder for mailbox in SHIPPED["extractors"]["email"]["mailboxes"] for folder in (mailbox["folders"] or [])
    }

    def test_the_inbox_folder_is_one_the_extractor_writes(self):
        assert SHIPPED_TRIAGE.inbox_folder in self.EXTRACTED

    def test_at_least_one_sent_folder_is_one_the_extractor_writes(self):
        assert set(SHIPPED_TRIAGE.sent_folders) & self.EXTRACTED
