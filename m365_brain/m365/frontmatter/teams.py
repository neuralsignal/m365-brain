"""Teams frontmatter builders (chats and channels)."""

from __future__ import annotations

from dataclasses import dataclass

from m365_brain.m365.markdown_writer import now_iso, short_hash, slugify


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
            "extractor": "m365-brain/teams_chats/2.0",
        },
        "status": "raw",
    }


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
        "status": "raw",
    }
