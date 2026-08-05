"""Tests for the Teams chat and channel frontmatter builders."""

from __future__ import annotations

import re

from hypothesis import given
from hypothesis import strategies as st

from m365_brain.config.index import RelationConfig
from m365_brain.m365.frontmatter.teams import (
    PARTICIPANT,
    TeamsChannelData,
    TeamsChatData,
    build_teams_channel_frontmatter,
    build_teams_chat_frontmatter,
    participant_relations,
)
from m365_brain.parsers.relations import parse_relations

COMMON_KEYS = {
    "title",
    "permalink",
    "type",
    "tags",
    "last_message_time",
    "message_count",
    "history_complete",
    "source",
    "status",
}

CHATS = st.builds(
    TeamsChatData,
    title=st.text(min_size=1, max_size=50),
    conversation_id=st.text(min_size=1, max_size=30),
    conversation_type=st.sampled_from(["oneOnOne", "group", "meeting"]),
    participants=st.lists(st.text(min_size=1, max_size=20), max_size=5),
    last_message_time=st.just("2026-03-12T10:00:00Z"),
    message_count=st.integers(min_value=0, max_value=10000),
    history_complete=st.booleans(),
)

CHANNELS = st.builds(
    TeamsChannelData,
    team_name=st.text(min_size=1, max_size=30),
    channel_name=st.text(min_size=1, max_size=30),
    channel_id=st.text(min_size=1, max_size=30),
    last_message_time=st.just("2026-03-12T10:00:00Z"),
    message_count=st.integers(min_value=0, max_value=10000),
    history_complete=st.booleans(),
)


class TestTeamsFrontmatterProperties:
    @given(CHATS)
    def test_chat_shape(self, data: TeamsChatData):
        fm = build_teams_chat_frontmatter(data)

        assert set(fm) == COMMON_KEYS | {"participants"}
        assert fm["type"] == "teams_chat"
        assert fm["status"] == "raw"
        assert fm["title"] == data.title
        assert fm["participants"] == data.participants
        assert fm["message_count"] == data.message_count
        assert fm["history_complete"] is data.history_complete
        assert fm["source"]["service"] == "teams"
        assert fm["source"]["extractor"] == "m365-brain/teams_chats/2.1"
        assert re.fullmatch(r"teams-chat-[a-z0-9-]+-[0-9a-f]{6}", fm["permalink"])

    @given(CHATS)
    def test_chat_tags_derive_from_conversation_type(self, data: TeamsChatData):
        fm = build_teams_chat_frontmatter(data)

        assert fm["tags"] == ["teams", f"teams-{data.conversation_type.lower()}"]

    @given(CHANNELS)
    def test_channel_shape(self, data: TeamsChannelData):
        fm = build_teams_channel_frontmatter(data)

        assert set(fm) == COMMON_KEYS | {"team", "channel"}
        assert fm["type"] == "teams_channel"
        assert fm["title"] == f"{data.team_name} / {data.channel_name}"
        assert fm["team"] == data.team_name
        assert fm["channel"] == data.channel_name
        assert fm["tags"] == ["teams", "teams-channel"]
        assert fm["source"]["extractor"] == "m365-brain/teams_channels/2.0"
        assert re.fullmatch(r"teams-channel-[a-z0-9-]+-[0-9a-f]{6}", fm["permalink"])


class TestTeamsFrontmatterShapes:
    def test_one_on_one_chat(self):
        fm = build_teams_chat_frontmatter(
            TeamsChatData(
                title="Alice Smith",
                conversation_id="chat-1",
                conversation_type="oneOnOne",
                participants=["Alice Smith", "Me"],
                last_message_time="2026-03-12T10:00:00Z",
                message_count=42,
                history_complete=True,
            )
        )

        assert fm["tags"] == ["teams", "teams-oneonone"]
        assert fm["permalink"].startswith("teams-chat-alice-smith-")
        assert fm["source"]["url"] is None, "the key is present-and-None, never absent"
        assert fm["source"]["id"] == "chat-1"

    def test_empty_conversation_type_yields_bare_prefix_tag(self):
        """The type is not guarded, so a missing type leaves a dangling `teams-` tag."""
        fm = build_teams_chat_frontmatter(
            TeamsChatData(
                title="Unknown chat",
                conversation_id="chat-2",
                conversation_type="",
                participants=[],
                last_message_time="",
                message_count=0,
                history_complete=False,
            )
        )

        assert fm["tags"] == ["teams", "teams-"]
        assert fm["participants"] == []
        assert fm["message_count"] == 0
        assert fm["history_complete"] is False
        assert fm["last_message_time"] == ""

    def test_channel_permalink_slugs_team_and_channel_together(self):
        fm = build_teams_channel_frontmatter(
            TeamsChannelData(
                team_name="Engineering Hub",
                channel_name="General",
                channel_id="ch-1",
                last_message_time="2026-03-12T10:00:00Z",
                message_count=3,
                history_complete=True,
            )
        )

        assert fm["title"] == "Engineering Hub / General"
        assert fm["permalink"].startswith("teams-channel-engineering-hub-general-")

    def test_channel_and_chat_key_sets_differ(self):
        """A channel carries `team`/`channel`; a chat carries `participants`. Neither carries both."""
        chat = build_teams_chat_frontmatter(
            TeamsChatData(
                title="Project sync",
                conversation_id="chat-3",
                conversation_type="group",
                participants=["Alice", "Bob"],
                last_message_time="2026-03-12T10:00:00Z",
                message_count=7,
                history_complete=False,
            )
        )
        channel = build_teams_channel_frontmatter(
            TeamsChannelData(
                team_name="Project",
                channel_name="Sync",
                channel_id="ch-3",
                last_message_time="2026-03-12T10:00:00Z",
                message_count=7,
                history_complete=False,
            )
        )

        assert "participants" not in channel
        assert "team" not in chat
        assert "channel" not in chat
        assert set(chat) - set(channel) == {"participants"}
        assert set(channel) - set(chat) == {"team", "channel"}


def _chat(participants: list[str]) -> TeamsChatData:
    return TeamsChatData(
        title="Project sync",
        conversation_id="chat-9",
        conversation_type="group",
        participants=participants,
        last_message_time="2026-03-12T10:00:00Z",
        message_count=3,
        history_complete=True,
    )


PLAIN_NAMES = st.lists(
    st.text(alphabet=st.characters(whitelist_categories=("Lu", "Ll")), min_size=1, max_size=12),
    max_size=5,
)
"""Letters only. A name containing `[` or a newline is not a wikilink target the
parser can read back, and inventing an escaping rule for one Graph never returns
would be code with no caller."""


class TestParticipantRelations:
    """The counterparty a chat states, in the one shape the index reads back.

    `participants` is a list, so it is metadata and no per-entity read can see
    it. Parsed with the real relation parser rather than a regex, so this is the
    same reading `ops tiers` does.
    """

    def test_one_edge_per_participant_named_as_written(self):
        parsed = parse_relations(
            "\n".join(participant_relations(_chat(["Ana Ruiz", "Bo Frey"]))),
            RelationConfig(explicit_default_type="relates_to", inline_type="links_to"),
        )

        assert [(edge.relation_type, edge.to_name) for edge in parsed] == [
            (PARTICIPANT, "Ana Ruiz"),
            (PARTICIPANT, "Bo Frey"),
        ]

    def test_a_short_name_is_an_edge_like_any_other(self):
        """The slug-length filter this replaces dropped `Bo Ng` and said nothing."""
        assert participant_relations(_chat(["Bo Ng"])) == [f"- {PARTICIPANT} [[Bo Ng]]"]

    def test_a_chat_with_no_participants_states_no_edges(self):
        assert participant_relations(_chat([])) == []

    def test_a_blank_display_name_is_not_an_empty_link(self):
        assert participant_relations(_chat(["", "Ana Ruiz"])) == [f"- {PARTICIPANT} [[Ana Ruiz]]"]

    @given(PLAIN_NAMES)
    def test_every_participant_survives_into_a_parsed_edge(self, participants: list[str]):
        parsed = parse_relations(
            "\n".join(participant_relations(_chat(participants))),
            RelationConfig(explicit_default_type="relates_to", inline_type="links_to"),
        )

        assert {edge.to_name for edge in parsed} == set(participants)
        assert {edge.relation_type for edge in parsed} <= {PARTICIPANT}
