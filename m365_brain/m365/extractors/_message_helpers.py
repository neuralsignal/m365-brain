"""Shared helpers for Teams message extractors (chats, channels).

Handles sender extraction, content conversion, and inline-image src rewriting
for Graph chat/channel message dicts.
"""

from __future__ import annotations

import re

from m365_brain.m365.converters.html_to_md import html_to_markdown

_HOSTED_SRC_RE = re.compile(
    r'(?P<prefix>src=["\'])(?P<url>[^"\']*?hostedContents/(?P<hid>[^/"\']+)/\$value[^"\']*?)(?P<suffix>["\'])',
    re.IGNORECASE,
)


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


def extract_content(msg: dict, hosted_map: dict[str, str]) -> str:
    """Extract and convert message content to markdown.

    ``hosted_map`` maps Teams hostedContent ids to local relative paths.
    Pass ``{}`` when inline-image download is disabled or unsupported by the
    caller; the function never mutates the map.
    """
    body = msg.get("body", {})
    content_type = body.get("contentType", "text")
    content = body.get("content", "")

    if not content:
        return ""

    if content_type == "html":
        content = rewrite_hosted_image_srcs(content, hosted_map)
        return html_to_markdown(content, strip_images=False)
    return content


def rewrite_hosted_image_srcs(html: str, hosted_map: dict[str, str]) -> str:
    """Replace Teams hostedContents ``<img src>`` URLs with local relative paths.

    Only ``src`` attributes pointing at ``.../hostedContents/{hid}/$value``
    are rewritten. Hosted ids that are not present in ``hosted_map`` are
    left untouched so the caller can decide how to surface the miss.
    """
    if not hosted_map:
        return html

    def _sub(match: re.Match[str]) -> str:
        hid = match.group("hid")
        local = hosted_map.get(hid)
        if local is None:
            return match.group(0)
        return f"{match.group('prefix')}{local}{match.group('suffix')}"

    return _HOSTED_SRC_RE.sub(_sub, html)
