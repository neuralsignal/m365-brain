"""Helpers for downloading Teams chat message attachments and inline images.

File attachments on Teams chat messages are ``reference`` entries that point at
OneDrive/SharePoint share URLs. Resolving them requires the
``Files.Read.All`` scope: we encode the share URL, look up the driveItem to
read its size and ``@microsoft.graph.downloadUrl``, then stream the bytes.

Inline images live in a separate ``hostedContents`` collection on each
message and must be fetched via ``.../hostedContents/{hid}/$value``.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path

import httpx
import structlog

from m365_extract.config import TeamsChatsExtractorConfig
from m365_extract.extractors._attachment_helpers import convert_and_store
from m365_extract.graph_client import GraphApiError, GraphClient
from m365_extract.storage.base import StorageBackend
from m365_extract.storage.exceptions import StorageError

log = structlog.get_logger()

# Teams chatMessage.attachment contentType values that never carry a file payload.
_SKIPPED_CONTENT_TYPES: frozenset[str] = frozenset(
    {
        "messageReference",
        "meetingReference",
        "forwardedMessageReference",
        "application/vnd.microsoft.card.adaptive",
    }
)

# HTTP statuses that mean the attachment will never become downloadable for this
# account (no access / gone), so retrying on later sync cycles is pure waste.
_PERMANENT_FAILURE_STATUSES: frozenset[int] = frozenset({403, 404})

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


@dataclass(frozen=True)
class AttachmentRef:
    """A downloaded Teams message attachment, ready to be linked from messages.md."""

    name: str
    relative_path: str
    converted_path: str | None


def _encode_share_url(url: str) -> str:
    """Encode a sharing URL for the ``/shares/{encoded}/driveItem`` endpoint."""
    return "u!" + base64.urlsafe_b64encode(url.encode("utf-8")).decode("ascii").rstrip("=")


def _sanitize_filename(name: str) -> str:
    """Strip directory components from an attachment name."""
    return Path(name).name


def _resolve_reference_bytes(
    client: GraphClient,
    content_url: str,
    max_bytes: int,
) -> bytes | None:
    """Resolve a Teams ``reference`` attachment to bytes via the shares endpoint.

    Returns ``None`` when the file exceeds ``max_bytes`` or lacks a download URL;
    in both cases a warning is emitted by the caller.
    """
    encoded = _encode_share_url(content_url)
    drive_item = client.get(f"/shares/{encoded}/driveItem", params=None)
    size = drive_item.get("size", 0)
    if size and size > max_bytes:
        log.warning(
            "teams_chats.attachment_too_large",
            size_bytes=size,
            max_bytes=max_bytes,
        )
        return None
    download_url = drive_item.get("@microsoft.graph.downloadUrl")
    if not download_url:
        log.warning("teams_chats.attachment_no_download_url")
        return None
    return client.get_bytes(download_url)


def download_message_attachments(
    client: GraphClient,
    storage: StorageBackend,
    msg: dict,
    chat_dir: str,
    config: TeamsChatsExtractorConfig,
    converters_config: dict,
    failed_attachments: dict[str, str],
) -> list[AttachmentRef]:
    """Download file attachments referenced by a Teams chat message.

    Returns one ``AttachmentRef`` per successfully written file so the chat
    renderer can emit inline links beneath the message. Skipped entries
    (message/meeting references, missing fields, oversized, transport errors)
    are logged and excluded.

    ``failed_attachments`` is the extractor's persistent skip-list (part of
    sync state), keyed ``"{msg_id}:{name}"``. Downloads that fail with a
    permanent status (403/404 — e.g. files in another user's OneDrive) are
    recorded there in place and never re-attempted on later sync cycles.
    """
    msg_id = msg.get("id", "")
    if not msg_id:
        return []

    attachments = msg.get("attachments") or []
    if not attachments:
        return []

    max_bytes = config.max_attachment_size_mb * 1024 * 1024
    refs: list[AttachmentRef] = []

    for att in attachments:
        ctype = att.get("contentType") or ""
        if ctype in _SKIPPED_CONTENT_TYPES:
            continue
        name = _sanitize_filename(att.get("name") or "")
        content_url = att.get("contentUrl") or ""
        if not name or not content_url:
            log.warning(
                "teams_chats.attachment_skipped_missing_fields",
                msg_id=msg_id,
                content_type=ctype,
            )
            continue
        if ctype != "reference":
            log.debug(
                "teams_chats.attachment_unsupported_type",
                msg_id=msg_id,
                content_type=ctype,
                name=name,
            )
            continue

        failure_key = f"{msg_id}:{name}"
        if failure_key in failed_attachments:
            log.debug(
                "teams_chats.attachment_skipped_previously_failed",
                msg_id=msg_id,
                name=name,
                error=failed_attachments[failure_key],
            )
            continue

        try:
            data = _resolve_reference_bytes(client, content_url, max_bytes)
        except GraphApiError as exc:
            if exc.status_code in _PERMANENT_FAILURE_STATUSES:
                failed_attachments[failure_key] = f"http_{exc.status_code}"
                log.warning(
                    "teams_chats.attachment_download_failed_permanently",
                    msg_id=msg_id,
                    name=name,
                    status=exc.status_code,
                    error=str(exc),
                )
            else:
                log.warning(
                    "teams_chats.attachment_download_failed",
                    msg_id=msg_id,
                    name=name,
                    error=str(exc),
                )
            continue
        except httpx.TransportError as exc:
            log.warning(
                "teams_chats.attachment_download_failed",
                msg_id=msg_id,
                name=name,
                error=str(exc),
            )
            continue
        if data is None:
            continue

        relative_path = f"attachments/{msg_id}/{name}"
        try:
            storage.write_bytes(f"{chat_dir}/{relative_path}", data)
        except (StorageError, OSError) as exc:
            log.warning(
                "teams_chats.attachment_write_failed",
                msg_id=msg_id,
                name=name,
                error=str(exc),
            )
            continue

        converted_rel: str | None = None
        ext = Path(name).suffix.lower()
        if ext and ext in config.attachment_convert_extensions:
            converted_rel = f"attachments_converted/{msg_id}/{name}.md"
            convert_and_store(
                storage,
                data,
                name,
                f"{chat_dir}/{converted_rel}",
                converters_config,
            )

        refs.append(AttachmentRef(name=name, relative_path=relative_path, converted_path=converted_rel))

    return refs


def download_inline_images(
    client: GraphClient,
    storage: StorageBackend,
    chat_id: str,
    msg: dict,
    chat_dir: str,
    config: TeamsChatsExtractorConfig,
) -> dict[str, str]:
    """Download inline images referenced from a Teams chat message body.

    Returns a map of ``hostedContent id -> relative storage path`` so the body
    renderer can rewrite ``<img src>`` URLs to point at the local copy. Returns
    an empty map when no hosted contents are present or downloads fail.
    """
    msg_id = msg.get("id", "")
    if not msg_id:
        return {}

    max_bytes = config.max_attachment_size_mb * 1024 * 1024
    hosted_map: dict[str, str] = {}

    try:
        items = list(
            client.get_paginated(
                f"/chats/{chat_id}/messages/{msg_id}/hostedContents",
                params={"$select": "id"},
                max_pages=client.max_pages,
            )
        )
    except GraphApiError as exc:
        log.warning(
            "teams_chats.hosted_contents_fetch_failed",
            msg_id=msg_id,
            error=str(exc),
        )
        return {}

    for idx, item in enumerate(items):
        hid = item.get("id", "")
        if not hid:
            continue
        try:
            data, content_type = client.get_bytes_with_content_type(
                f"/chats/{chat_id}/messages/{msg_id}/hostedContents/{hid}/$value"
            )
        except (GraphApiError, httpx.TransportError) as exc:
            log.warning(
                "teams_chats.hosted_content_download_failed",
                msg_id=msg_id,
                hid=hid,
                error=str(exc),
            )
            continue
        if len(data) > max_bytes:
            log.warning(
                "teams_chats.hosted_content_too_large",
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
            storage.write_bytes(f"{chat_dir}/{relative_path}", data)
        except (StorageError, OSError) as exc:
            log.warning(
                "teams_chats.hosted_content_write_failed",
                msg_id=msg_id,
                hid=hid,
                error=str(exc),
            )
            continue
        hosted_map[hid] = relative_path

    return hosted_map
