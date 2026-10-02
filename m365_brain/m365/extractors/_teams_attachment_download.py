"""Low-level download resolution for Teams message attachments.

Encodes sharing URLs and resolves them to raw bytes via the Graph
``/shares/{encoded}/driveItem`` endpoint.
"""

from __future__ import annotations

import base64

import structlog

from m365_brain.m365.client import GraphClient

log = structlog.get_logger()


def encode_share_url(url: str) -> str:
    """Encode a sharing URL for the ``/shares/{encoded}/driveItem`` endpoint."""
    return "u!" + base64.urlsafe_b64encode(url.encode("utf-8")).decode("ascii").rstrip("=")


def resolve_reference_bytes(
    client: GraphClient,
    content_url: str,
    max_bytes: int,
) -> bytes | None:
    """Resolve a Teams ``reference`` attachment to bytes via the shares endpoint.

    Returns ``None`` when the file exceeds ``max_bytes`` or lacks a download URL;
    in both cases a warning is emitted by the caller.
    """
    encoded = encode_share_url(content_url)
    drive_item = client.get(f"/shares/{encoded}/driveItem", params=None)
    size = drive_item.get("size", 0)
    if size and size > max_bytes:
        log.warning(
            "teams_attachments.attachment_too_large",
            size_bytes=size,
            max_bytes=max_bytes,
        )
        return None
    download_url = drive_item.get("@microsoft.graph.downloadUrl")
    if not download_url:
        log.warning("teams_attachments.attachment_no_download_url")
        return None
    return client.get_bytes(download_url)
