"""Shared helpers for Teams message extractors (chats, channels).

Handles sender extraction and content conversion from Graph API message dicts.
"""

from __future__ import annotations

from m365_extract.converters.html_to_md import html_to_markdown


def extract_sender(msg: dict) -> str:
    """Extract the sender display name from a Graph chat/channel message."""
    from_field = msg.get("from")
    if not from_field:
        return ""
    user = from_field.get("user")
    if user:
        return user.get("displayName", "")
    app = from_field.get("application")
    if app:
        return app.get("displayName", "Bot")
    return ""


def extract_content(msg: dict) -> str:
    """Extract and convert message content to markdown."""
    body = msg.get("body", {})
    content_type = body.get("contentType", "text")
    content = body.get("content", "")

    if not content:
        return ""

    if content_type == "html":
        return html_to_markdown(content)
    return content
