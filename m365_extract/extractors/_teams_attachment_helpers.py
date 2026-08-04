"""Helpers for downloading Teams message file attachments.

File attachments on Teams messages are ``reference`` entries that point at
OneDrive/SharePoint share URLs. Resolving them requires the
``Files.Read.All`` scope: we encode the share URL, look up the driveItem to
read its size and ``@microsoft.graph.downloadUrl``, then stream the bytes.

Inline-image (hostedContents) download lives in
``m365_extract.extractors._teams_hosted_content``.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import httpx
import structlog

from m365_extract.extractors._attachment_helpers import convert_and_store
from m365_extract.graph_client import GraphApiError, GraphClient
from m365_extract.storage.exceptions import StorageError

if TYPE_CHECKING:
    from m365_extract.extractors._teams_ingest import TeamsContext

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


class AttachmentSettings(Protocol):
    """The attachment config fields shared by chat and channel extractor configs."""

    @property
    def download_attachments(self) -> bool: ...

    @property
    def download_inline_images(self) -> bool: ...

    @property
    def max_attachment_size_mb(self) -> int: ...

    @property
    def attachment_convert_extensions(self) -> list[str]: ...


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


def downloadable_attachment_names(msg: dict, failed_attachments: dict[str, str]) -> set[str]:
    """Names that ``download_message_attachments`` would attempt to download.

    Mirrors its filter exactly (``reference`` type, name and contentUrl
    present, not on the permanent-failure skip-list) so callers can detect
    whether a message's downloadable set changed without doing any I/O.
    ``test_teams_ingest.py`` pins the equivalence between the two.
    """
    msg_id = msg.get("id", "")
    if not msg_id:
        return set()
    names: set[str] = set()
    for att in msg.get("attachments") or []:
        if (att.get("contentType") or "") != "reference":
            continue
        name = _sanitize_filename(att.get("name") or "")
        if not name or not (att.get("contentUrl") or ""):
            continue
        if f"{msg_id}:{name}" in failed_attachments:
            continue
        names.add(name)
    return names


def download_message_attachments(
    ctx: TeamsContext,
    msg: dict,
) -> list[AttachmentRef]:
    """Download file attachments referenced by a Teams message.

    Returns one ``AttachmentRef`` per successfully written file so the
    renderer can emit inline links beneath the message. Skipped entries
    (message/meeting references, missing fields, oversized, transport errors)
    are logged and excluded.

    ``ctx.failed_attachments`` is the extractor's persistent skip-list (part of
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

    max_bytes = ctx.settings.max_attachment_size_mb * 1024 * 1024
    refs: list[AttachmentRef] = []

    for att in attachments:
        ctype = att.get("contentType") or ""
        if ctype in _SKIPPED_CONTENT_TYPES:
            continue
        name = _sanitize_filename(att.get("name") or "")
        content_url = att.get("contentUrl") or ""
        if not name or not content_url:
            log.warning(
                "teams_attachments.attachment_skipped_missing_fields",
                msg_id=msg_id,
                content_type=ctype,
            )
            continue
        if ctype != "reference":
            log.debug(
                "teams_attachments.attachment_unsupported_type",
                msg_id=msg_id,
                content_type=ctype,
                name=name,
            )
            continue

        failure_key = f"{msg_id}:{name}"
        if failure_key in ctx.failed_attachments:
            log.debug(
                "teams_attachments.attachment_skipped_previously_failed",
                msg_id=msg_id,
                name=name,
                error=ctx.failed_attachments[failure_key],
            )
            continue

        try:
            data = _resolve_reference_bytes(ctx.client, content_url, max_bytes)
        except GraphApiError as exc:
            if exc.status_code in _PERMANENT_FAILURE_STATUSES:
                ctx.failed_attachments[failure_key] = f"http_{exc.status_code}"
                log.warning(
                    "teams_attachments.attachment_download_failed_permanently",
                    msg_id=msg_id,
                    name=name,
                    status=exc.status_code,
                    error=str(exc),
                )
            else:
                log.warning(
                    "teams_attachments.attachment_download_failed",
                    msg_id=msg_id,
                    name=name,
                    error=str(exc),
                )
            continue
        except httpx.TransportError as exc:
            log.warning(
                "teams_attachments.attachment_download_failed",
                msg_id=msg_id,
                name=name,
                error=str(exc),
            )
            continue
        if data is None:
            continue

        relative_path = f"attachments/{msg_id}/{name}"
        try:
            ctx.storage.write_bytes(f"{ctx.conv_dir}/{relative_path}", data)
        except (StorageError, OSError) as exc:
            log.warning(
                "teams_attachments.attachment_write_failed",
                msg_id=msg_id,
                name=name,
                error=str(exc),
            )
            continue

        converted_rel: str | None = None
        ext = Path(name).suffix.lower()
        if ext and ext in ctx.settings.attachment_convert_extensions:
            converted_rel = f"attachments_converted/{msg_id}/{name}.md"
            if not convert_and_store(
                ctx.storage,
                data,
                name,
                f"{ctx.conv_dir}/{converted_rel}",
                ctx.converters_config,
            ):
                converted_rel = None

        refs.append(AttachmentRef(name=name, relative_path=relative_path, converted_path=converted_rel))

    return refs
