"""HTML to markdown conversion via markdownify."""

from __future__ import annotations

from markdownify import markdownify


def html_to_markdown(html: str) -> str:
    """Convert an HTML string to clean markdown.

    Used for Outlook email HTML bodies and Teams message content.
    """
    if not html or not html.strip():
        return ""
    return markdownify(html, heading_style="ATX", strip=["img"]).strip()
