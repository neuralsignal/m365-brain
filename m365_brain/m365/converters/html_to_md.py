"""HTML to markdown conversion via markdownify."""

from __future__ import annotations

from markdownify import markdownify


def html_to_markdown(html: str, strip_images: bool) -> str:
    """Convert an HTML string to clean markdown.

    Used for Outlook email HTML bodies and Teams message content.

    ``strip_images`` controls whether ``<img>`` tags are dropped. Email and
    calendar bodies set this to True so externally-hosted images do not leak
    into notes; Teams chat content sets it to False because inline ``<img>``
    src URLs are rewritten ahead of conversion to point at locally-downloaded
    copies of the image bytes.
    """
    if not html or not html.strip():
        return ""
    options: dict = {"heading_style": "ATX"}
    if strip_images:
        options["strip"] = ["img"]
    return markdownify(html, **options).strip()
