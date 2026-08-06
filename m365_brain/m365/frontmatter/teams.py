"""Teams frontmatter builders (chats and channels), and the fact frontmatter cannot hold.

A chat's counterparties are its participants, and there are N of them. A
frontmatter key holds one value, and `m365_brain/parsers/document.py` promotes
only a **scalar** key to an observation -- a list stays in metadata, which no
per-entity read can reach. So `participants: [...]` renders for a human and is
invisible to `ops tiers`, exactly as `attendees` was on a calendar event.

Joining the names into one string is the wrong repair, for the reason it was
wrong there: `ops tiers` groups on the whole value, so a joined one becomes a
single counterparty called "Ana Ruiz, Bo Frey". The shape that carries N
readable counterparties is a body relation, one line per participant --
`participant_relations` below -- which is what
`ops.tiers.interaction_sources[].party_from.relation` names.

A channel states no counterparty at all: `TeamsChannelData` carries a team and a
channel, not people, so there is nothing here for a channel to emit and no
`teams_channel` interaction source in the shipped template.
"""

from __future__ import annotations

from dataclasses import dataclass

from m365_brain.m365.markdown_writer import now_iso, short_hash, slugify

PARTICIPANT = "participant"
"""The relation type each participant edge is written under.

This extractor's vocabulary, like every frontmatter key in this module, so it is
a literal rather than config -- and a named one, because the config that reads
it (`ops.tiers.interaction_sources`) has to spell the same word and a grep for
it should find both ends.
"""


@dataclass(frozen=True)
class TeamsChatData:
    title: str
    conversation_id: str
    conversation_type: str
    participants: list[str]
    last_message_time: str
    message_count: int
    history_complete: bool


@dataclass(frozen=True)
class TeamsChannelData:
    team_name: str
    channel_name: str
    channel_id: str
    last_message_time: str
    message_count: int
    history_complete: bool


def build_teams_chat_frontmatter(data: TeamsChatData) -> dict:
    """Build frontmatter dict for a Teams chat conversation."""
    slug = slugify(data.title, 80)
    permalink = f"teams-chat-{slug}-{short_hash(data.conversation_id, 6)}"
    tags = ["teams", f"teams-{data.conversation_type.lower()}"]
    return {
        "title": data.title,
        "permalink": permalink,
        "type": "teams_chat",
        "tags": tags,
        "participants": data.participants,
        "last_message_time": data.last_message_time,
        "message_count": data.message_count,
        "history_complete": data.history_complete,
        "source": {
            "system": "microsoft365",
            "service": "teams",
            "id": data.conversation_id,
            # No web link on a synced conversation. The key stays so `source`
            # has one shape across every entity type.
            "url": None,
            "extracted_at": now_iso(),
            "extractor": "m365-brain/teams_chats/2.1",
        },
    }


def participant_relations(data: TeamsChatData) -> list[str]:
    """One `- participant [[Name]]` line per participant.

    Written into the markdown body, because that is the only place a chat can
    state N counterparties in a shape the index reads back -- see the module
    docstring.

    The link names the participant as Graph spelled them, not a
    `contact-<slug>` placeholder. A slug is `ops.link_resolution`'s spelling for
    a link that can never resolve, and it also becomes the counterparty `ops
    tiers` reports, so the same person seen in a chat and on a calendar event
    would not have been one counterparty.
    """
    return [f"- {PARTICIPANT} [[{name}]]" for name in data.participants if name]


def build_teams_channel_frontmatter(data: TeamsChannelData) -> dict:
    """Build frontmatter dict for a Teams channel."""
    slug = slugify(f"{data.team_name}-{data.channel_name}", 80)
    permalink = f"teams-channel-{slug}-{short_hash(data.channel_id, 6)}"
    return {
        "title": f"{data.team_name} / {data.channel_name}",
        "permalink": permalink,
        "type": "teams_channel",
        "tags": ["teams", "teams-channel"],
        "team": data.team_name,
        "channel": data.channel_name,
        "last_message_time": data.last_message_time,
        "message_count": data.message_count,
        "history_complete": data.history_complete,
        "source": {
            "system": "microsoft365",
            "service": "teams",
            "id": data.channel_id,
            "url": None,
            "extracted_at": now_iso(),
            "extractor": "m365-brain/teams_channels/2.0",
        },
    }
