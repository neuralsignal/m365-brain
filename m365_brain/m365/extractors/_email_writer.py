"""Single-email rendering: parse a Graph message dict, build markdown, store."""

from __future__ import annotations

import structlog

from m365_brain.config import EmailExtractorConfig
from m365_brain.m365.client import GraphClient
from m365_brain.m365.converters.html_to_md import html_to_markdown
from m365_brain.m365.extractors._attachment_helpers import download_attachments
from m365_brain.m365.extractors.base import ExtractorContext
from m365_brain.m365.frontmatter import EmailData, build_email_frontmatter
from m365_brain.m365.markdown_writer import dumps_markdown, short_hash, slugify
from m365_brain.storage.base import StorageBackend

log = structlog.get_logger()

EXTRACTOR_NAME = "email"


def write_email(
    storage: StorageBackend,
    client: GraphClient,
    msg: dict,
    folder: str,
    address: str,
    output_subdir: str,
    endpoint_base: str,
    config: EmailExtractorConfig,
    ctx: ExtractorContext,
    seen_keys: set[tuple[str, str]],
    path_map: dict[str, str],
) -> bool:
    """Write a single email to storage. Returns True if written."""
    message_id = msg.get("id", "")
    conversation_id = msg.get("conversationId", "")
    subject = msg.get("subject") or "(no subject)"
    received = msg.get("receivedDateTime", "")

    if not message_id or not received:
        log.warning("email.skipping_invalid", message_id=message_id)
        return False

    slug = slugify(subject, 80)
    key = (received[:16], slug)
    if key in seen_keys:
        log.info("email.skipped_duplicate", slug=slug, received=received[:16])
        return False
    seen_keys.add(key)

    from_field = (msg.get("from") or {}).get("emailAddress", {})
    sender_address = from_field.get("address", "")
    sender_name = from_field.get("name", "")

    to_recipients = [r.get("emailAddress", {}).get("address", "") for r in msg.get("toRecipients", [])]

    body_obj = msg.get("body", {})
    content_type = body_obj.get("contentType", "text")
    raw_body = body_obj.get("content", "")

    if content_type == "html":
        body_md = html_to_markdown(raw_body, strip_images=True)
    else:
        body_md = raw_body

    fm = build_email_frontmatter(
        EmailData(
            subject=subject,
            message_id=message_id,
            conversation_id=conversation_id,
            received_time=received,
            folder=folder,
            mailbox=address,
            sender_address=sender_address,
            sender_name=sender_name,
            to_recipients=to_recipients,
            importance=msg.get("importance", "normal"),
            has_attachments=msg.get("hasAttachments", False),
            web_link=msg.get("webLink", ""),
        )
    )

    body_parts = [f"# {subject}\n"]
    body_parts.append(f"**From:** {sender_name} <{sender_address}>")
    if to_recipients:
        body_parts.append(f"**To:** {', '.join(to_recipients)}")
    body_parts.append(f"**Date:** {received}\n")
    body_parts.append("---\n")
    body_parts.append(body_md)

    content = dumps_markdown(fm, "\n".join(body_parts))

    date_str = received[:10]
    year = date_str[:4]
    hsh = short_hash(message_id, 6)
    subdir = output_subdir.strip("/")
    segments = [subdir] if subdir else []
    segments += [year, date_str, f"{slug}-{hsh}"]
    email_dir = ctx.paths.inbox_item(EXTRACTOR_NAME, *segments)
    file_path = ctx.paths.entry_file(email_dir)

    storage.write_file(file_path, content)
    path_map[message_id] = email_dir

    if config.download_attachments and msg.get("hasAttachments"):
        download_attachments(
            client,
            storage,
            endpoint_base,
            message_id,
            email_dir,
            config,
            ctx,
        )

    return True
