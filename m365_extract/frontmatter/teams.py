"""Teams frontmatter builders (chats and channels)."""

from __future__ import annotations

from dataclasses import dataclass

from m365_extract.markdown_writer import now_iso, short_hash, slugify


@dataclass(frozen=True)
class TeamsChatData:
    title: str
    conversation_id: str
    conversation_type: str
    participants: list[str]
    last_message_time: str
    message_limit_reached: bool


@dataclass(frozen=True)
class TeamsChannelData:
    team_name: str
    channel_name: str
    channel_id: str
    last_message_time: str


def build_teams_chat_frontmatter(data: TeamsChatData) -> dict:
    """Build frontmatter dict for a Teams chat conversation."""
    slug = slugify(data.title, 80)
    permalink = f"teams-chat-{slug}-{short_hash(data.conversation_id, 6)}"
    tags = ["teams", f"teams-{data.conversation_type.lower()}"]
    fm: dict = {
        "title": data.title,
        "permalink": permalink,
        "type": "teams_chat",
        "tags": tags,
        "participants": data.participants,
        "last_message_time": data.last_message_time,
        "source": {
            "system": "microsoft365",
            "service": "teams",
            "id": data.conversation_id,
            "extracted_at": now_iso(),
            "extractor": "m365-extract/teams_chats/1.0",
        },
        "status": "raw",
    }
    if data.message_limit_reached:
        fm["message_limit_reached"] = True
    return fm


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
        "source": {
            "system": "microsoft365",
            "service": "teams",
            "id": data.channel_id,
            "extracted_at": now_iso(),
            "extractor": "m365-extract/teams_channels/1.0",
        },
        "status": "raw",
    }
