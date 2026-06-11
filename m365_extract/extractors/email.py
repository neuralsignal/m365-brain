"""Email extractor — syncs emails via Graph API delta queries.

Reads from {/me | /users/{address}}/mailFolders/{folder}/messages/delta for each
configured (mailbox, folder) pair. Writes Obsidian-compatible markdown files
with YAML frontmatter, namespaced under emails/{output_subdir}/ when set.
Downloads and optionally converts email attachments.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import structlog

from m365_extract.config import EmailExtractorConfig, MailboxConfig
from m365_extract.converters.html_to_md import html_to_markdown
from m365_extract.extractors._attachment_helpers import download_attachments
from m365_extract.extractors._folder_helpers import (
    FOLDER_IDS,
    cache_folder_id,
    list_all_folders,
    resolve_folder_id,
)
from m365_extract.frontmatter import EmailData, build_email_frontmatter
from m365_extract.graph_client import GraphClient
from m365_extract.markdown_writer import dumps_markdown, short_hash, slugify
from m365_extract.storage.base import StorageBackend

log = structlog.get_logger()

name = "email"
required_scopes = ["Mail.Read"]

# Sentinel meaning "the authenticated user's mailbox"; uses /me/* endpoints.
_ME = "me"

# Page size requested via $top on delta queries. The per-cycle page budget is
# derived from it: ceil(max_items_per_sync / page size) pages per folder. A
# round interrupted by the budget resumes from the pending nextLink next cycle.
_DELTA_PAGE_SIZE = 50


def _endpoint_base(address: str) -> str:
    """Return the Graph API root path for a mailbox.

    `"me"` → `/me`; any other value is treated as a user UPN or object ID and
    yields `/users/{address}`.
    """
    if address == _ME:
        return "/me"
    return f"/users/{address}"


def run(
    client: GraphClient,
    storage: StorageBackend,
    state: dict,
    config: EmailExtractorConfig,
    converters_config: dict,
) -> tuple[dict, int]:
    """Extract emails from every (mailbox, folder) pair using delta queries.

    Returns (updated_state, total_items_written).
    """
    total_written = 0
    seen_keys: set[tuple[str, str]] = set()

    for mailbox in config.mailboxes:
        folders = _folders_for_mailbox(client, mailbox)

        for folder in folders:
            state_key = f"delta_link_{mailbox.address}_{folder}"
            delta_link = state.get(state_key)

            items, new_delta_link = _sync_folder(
                client,
                storage,
                mailbox.address,
                mailbox.output_subdir,
                folder,
                delta_link,
                config,
                converters_config,
                seen_keys,
            )

            if new_delta_link:
                state[state_key] = new_delta_link

            total_written += items

    state["last_sync"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    log.info("email.sync_complete", total_written=total_written)
    return state, total_written


def _folders_for_mailbox(client: GraphClient, mailbox: MailboxConfig) -> list[str]:
    """Resolve the folder display-name list for a mailbox.

    If `mailbox.folders` is None, auto-discover via Graph API (and prime the
    folder-ID cache). Otherwise return the configured list verbatim.
    """
    if mailbox.folders is not None:
        return list(mailbox.folders)
    discovered = list_all_folders(client, _endpoint_base(mailbox.address), mailbox.address)
    for display, folder_id in discovered:
        if display not in FOLDER_IDS:
            cache_folder_id(mailbox.address, display, folder_id)
    return [display for display, _ in discovered]


def _sync_folder(
    client: GraphClient,
    storage: StorageBackend,
    address: str,
    output_subdir: str,
    folder: str,
    delta_link: str | None,
    config: EmailExtractorConfig,
    converters_config: dict,
    seen_keys: set[tuple[str, str]],
) -> tuple[int, str | None]:
    """Sync a single (mailbox, folder). Returns (items_written, new_delta_link)."""
    endpoint_base = _endpoint_base(address)
    folder_id = resolve_folder_id(client, endpoint_base, address, folder)
    path = f"{endpoint_base}/mailFolders/{folder_id}/messages/delta"

    sync_type = "incremental" if delta_link else "initial"
    log.info("email.folder_sync_start", mailbox=address, folder=folder, sync_type=sync_type)

    params = {
        "$select": "id,subject,bodyPreview,body,from,toRecipients,ccRecipients,"
        "receivedDateTime,importance,hasAttachments,webLink,parentFolderId",
        "$top": str(_DELTA_PAGE_SIZE),
    }

    if not delta_link:
        cutoff = datetime.now(UTC).replace(
            hour=0,
            minute=0,
            second=0,
            microsecond=0,
        )
        cutoff = cutoff - timedelta(days=config.lookback_days)
        params["$filter"] = f"receivedDateTime ge {cutoff.strftime('%Y-%m-%dT%H:%M:%SZ')}"

    # The page budget bounds per-cycle work; everything fetched IS processed.
    # Slicing after the fetch would skip the tail forever once the (resume)
    # delta link is persisted.
    max_pages = max(1, math.ceil(config.max_items_per_sync / _DELTA_PAGE_SIZE))
    messages, new_delta_link = client.get_delta(path, delta_link, params=params, max_pages=max_pages)

    written = 0
    for msg in messages:
        if _write_email(
            storage,
            client,
            msg,
            folder,
            address,
            output_subdir,
            config,
            converters_config,
            seen_keys,
        ):
            written += 1

    log.info(
        "email.folder_synced",
        mailbox=address,
        folder=folder,
        sync_type=sync_type,
        fetched=len(messages),
        written=written,
    )
    return written, new_delta_link


def _write_email(
    storage: StorageBackend,
    client: GraphClient,
    msg: dict,
    folder: str,
    address: str,
    output_subdir: str,
    config: EmailExtractorConfig,
    converters_config: dict,
    seen_keys: set[tuple[str, str]],
) -> bool:
    """Write a single email to storage. Returns True if written."""
    message_id = msg.get("id", "")
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
    if subdir:
        email_dir = f"emails/{subdir}/{year}/{date_str}/{slug}-{hsh}"
    else:
        email_dir = f"emails/{year}/{date_str}/{slug}-{hsh}"
    file_path = f"{email_dir}/index.md"

    storage.write_file(file_path, content)

    if config.download_attachments and msg.get("hasAttachments"):
        download_attachments(
            client,
            storage,
            _endpoint_base(address),
            message_id,
            email_dir,
            config,
            converters_config,
        )

    return True
