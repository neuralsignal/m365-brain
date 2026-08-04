"""Inline-image (hostedContents) download helper for Teams messages.

Inline images live in a ``hostedContents`` collection on each chat or channel
message and must be fetched via ``{message_api_base}/hostedContents/{hid}/$value``.
The caller supplies ``message_api_base`` (e.g. ``/chats/{cid}/messages/{mid}``
or ``/teams/{tid}/channels/{cid}/messages/{mid}/replies/{rid}``) so the same
helper serves chats, channel roots, and channel replies.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import structlog

from m365_extract.graph_client import GraphApiError
from m365_extract.storage.exceptions import StorageError

if TYPE_CHECKING:
    from m365_extract.extractors._teams_ingest import TeamsContext

log = structlog.get_logger()

# Map response Content-Type (lower-cased, no params) to file extension.
_CONTENT_TYPE_EXT: dict[str, str] = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/svg+xml": ".svg",
    "image/bmp": ".bmp",
}


def download_inline_images(
    ctx: TeamsContext,
    message_api_base: str,
    msg: dict,
) -> dict[str, str]:
    """Download inline images referenced from a Teams message body.

    Returns a map of ``hostedContent id -> relative storage path`` so the body
    renderer can rewrite ``<img src>`` URLs to point at the local copy. Returns
    an empty map when no hosted contents are present or downloads fail.
    """
    msg_id = msg.get("id", "")
    if not msg_id:
        return {}

    max_bytes = ctx.settings.max_attachment_size_mb * 1024 * 1024
    hosted_map: dict[str, str] = {}

    try:
        items = list(
            ctx.client.get_paginated(
                f"{message_api_base}/hostedContents",
                params={"$select": "id"},
                max_pages=ctx.client.max_pages,
            )
        )
    except GraphApiError as exc:
        log.warning(
            "teams_hosted_content.fetch_failed",
            msg_id=msg_id,
            error=str(exc),
        )
        return {}

    for idx, item in enumerate(items):
        hid = item.get("id", "")
        if not hid:
            continue
        try:
            data, content_type = ctx.client.get_bytes_with_content_type(
                f"{message_api_base}/hostedContents/{hid}/$value"
            )
        except (GraphApiError, httpx.TransportError) as exc:
            log.warning(
                "teams_hosted_content.download_failed",
                msg_id=msg_id,
                hid=hid,
                error=str(exc),
            )
            continue
        if len(data) > max_bytes:
            log.warning(
                "teams_hosted_content.too_large",
                msg_id=msg_id,
                hid=hid,
                size_bytes=len(data),
                max_bytes=max_bytes,
            )
            continue

        mime = content_type.split(";", 1)[0].strip().lower()
        ext = _CONTENT_TYPE_EXT.get(mime, ".bin")
        filename = f"inline_{idx}{ext}"
        relative_path = f"attachments/{msg_id}/{filename}"
        try:
            ctx.storage.write_bytes(f"{ctx.conv_dir}/{relative_path}", data)
        except (StorageError, OSError) as exc:
            log.warning(
                "teams_hosted_content.write_failed",
                msg_id=msg_id,
                hid=hid,
                error=str(exc),
            )
            continue
        hosted_map[hid] = relative_path

    return hosted_map
