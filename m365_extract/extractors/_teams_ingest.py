"""Shared Graph-payload→StoredMessage conversion for the Teams extractors.

One source of truth — used by both ``teams_chats`` and ``teams_channels`` —
for the etag-freshness check, the message conversion (with media downloads),
and the media-reuse rule that stops metadata-only changes (e.g. reactions)
from re-downloading attachments and inline images.
"""

from __future__ import annotations

from dataclasses import asdict

import structlog

from m365_extract.extractors._message_helpers import extract_content, extract_sender
from m365_extract.extractors._message_store import StoredMessage
from m365_extract.extractors._teams_attachment_helpers import (
    AttachmentSettings,
    download_message_attachments,
    downloadable_attachment_names,
)
from m365_extract.extractors._teams_hosted_content import download_inline_images
from m365_extract.graph_client import GraphClient
from m365_extract.storage.base import StorageBackend

log = structlog.get_logger()

# Documented Graph $top maximum for Teams message endpoints — an API protocol
# limit, not a config value. Also drives backfill page math in both extractors.
GRAPH_PAGE_SIZE = 50


def is_etag_fresh(existing: StoredMessage | None, msg: dict) -> bool:
    """True when the stored copy already matches the Graph payload's etag."""
    return existing is not None and existing.etag == msg.get("etag", "")


def _can_reuse_media(msg: dict, prior: StoredMessage | None, failed_attachments: dict[str, str]) -> bool:
    """Decide whether the prior content and attachment refs survive an etag bump.

    Reaction-only changes bump ``etag``/``lastModifiedDateTime`` without
    touching the body or attachments. Re-downloading is wasteful and — worse —
    a download failure would replace the stored message with fewer attachment
    refs (permanent link loss in the store and rendered markdown). Reuse
    requires: a prior copy, neither side a tombstone, an unedited body
    (``lastEditedDateTime`` null — the prior content, including its rewritten
    inline-image links, is still valid), and an unchanged downloadable
    attachment name set.
    """
    if prior is None or prior.deleted:
        return False
    if msg.get("deletedDateTime") is not None or msg.get("lastEditedDateTime") is not None:
        return False
    prior_names = {att["name"] for att in prior.attachments}
    return downloadable_attachment_names(msg, failed_attachments) == prior_names


def to_stored_message(
    client: GraphClient,
    storage: StorageBackend,
    msg: dict,
    parent_id: str | None,
    message_api_base: str,
    conv_dir: str,
    settings: AttachmentSettings,
    converters_config: dict,
    failed_attachments: dict[str, str],
    prior: StoredMessage | None,
) -> StoredMessage:
    """Convert a Graph chat/channel message to a StoredMessage, downloading its media.

    ``message_api_base`` is the message's own Graph path (e.g.
    ``/chats/{cid}/messages/{mid}`` or
    ``/teams/{tid}/channels/{cid}/messages/{mid}/replies/{rid}``), used for the
    hostedContents route. When ``prior`` is reusable (see ``_can_reuse_media``)
    the prior content and attachment refs are kept and no downloads run.
    """
    if _can_reuse_media(msg, prior, failed_attachments):
        content = prior.content  # type: ignore[union-attr]  # guarded by _can_reuse_media
        attachments = prior.attachments  # type: ignore[union-attr]
        log.debug("teams_ingest.media_reused", msg_id=msg.get("id", ""))
    else:
        if settings.download_inline_images:
            hosted_map = download_inline_images(client, storage, message_api_base, msg, conv_dir, settings)
        else:
            hosted_map = {}
        if settings.download_attachments:
            refs = download_message_attachments(
                client, storage, msg, conv_dir, settings, converters_config, failed_attachments
            )
        else:
            refs = []
        content = extract_content(msg, hosted_map)
        attachments = [asdict(r) for r in refs]

    created = msg.get("createdDateTime", "")
    return StoredMessage(
        id=msg.get("id", ""),
        parent_id=parent_id,
        sender=extract_sender(msg),
        created=created,
        last_modified=msg.get("lastModifiedDateTime") or created,
        etag=msg.get("etag", ""),
        edited=msg.get("lastEditedDateTime") is not None,
        deleted=msg.get("deletedDateTime") is not None,
        content=content,
        attachments=attachments,
        subject=msg.get("subject") if parent_id is None else None,
    )
