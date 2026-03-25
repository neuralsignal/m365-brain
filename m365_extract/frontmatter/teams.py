"""Teams frontmatter builders (chats and channels)."""

from __future__ import annotations

from m365_extract.markdown_writer import now_iso, short_hash, slugify


def build_teams_chat_frontmatter(
    *,
    title: str,
    conversation_id: str,
    conversation_type: str,
    participants: list[str],
    last_message_time: str,
    message_limit_reached: bool,
) -> dict:
    """Build frontmatter dict for a Teams chat conversation."""
    slug = slugify(title, 80)
    permalink = f"teams-chat-{slug}-{short_hash(conversation_id, 6)}"
    tags = ["teams", f"teams-{conversation_type.lower()}"]
    fm: dict = {
        "title": title,
        "permalink": permalink,
        "type": "teams_chat",
        "tags": tags,
        "participants": participants,
        "last_message_time": last_message_time,
        "source": {
            "system": "microsoft365",
            "service": "teams",
            "id": conversation_id,
            "extracted_at": now_iso(),
            "extractor": "m365-extract/teams_chats/1.0",
        },
        "status": "raw",
    }
    if message_limit_reached:
        fm["message_limit_reached"] = True
    return fm


def build_teams_channel_frontmatter(
    *,
    team_name: str,
    channel_name: str,
    channel_id: str,
    last_message_time: str,
) -> dict:
    """Build frontmatter dict for a Teams channel."""
    slug = slugify(f"{team_name}-{channel_name}", 80)
    permalink = f"teams-channel-{slug}-{short_hash(channel_id, 6)}"
    return {
        "title": f"{team_name} / {channel_name}",
        "permalink": permalink,
        "type": "teams_channel",
        "tags": ["teams", "teams-channel"],
        "team": team_name,
        "channel": channel_name,
        "last_message_time": last_message_time,
        "source": {
            "system": "microsoft365",
            "service": "teams",
            "id": channel_id,
            "extracted_at": now_iso(),
            "extractor": "m365-extract/teams_channels/1.0",
        },
        "status": "raw",
    }
