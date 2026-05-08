"""Email extractor — syncs emails via Graph API delta queries.

Reads from /me/mailFolders/{folder}/messages/delta for each configured folder.
Writes Obsidian-compatible markdown files with YAML frontmatter.
Downloads and optionally converts email attachments.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import structlog

from m365_extract.config import EmailExtractorConfig
from m365_extract.converters.html_to_md import html_to_markdown
from m365_extract.extractors._attachment_helpers import download_attachments as _download_attachments
from m365_extract.frontmatter import build_email_frontmatter
from m365_extract.graph_client import GraphApiError, GraphClient
from m365_extract.markdown_writer import dumps_markdown, short_hash, slugify
from m365_extract.storage.base import StorageBackend

log = structlog.get_logger()

name = "email"
required_scopes = ["Mail.Read"]

# Graph API folder name → delta endpoint name mapping
_FOLDER_IDS = {
    "Inbox": "Inbox",
    "SentItems": "SentItems",
    "Drafts": "Drafts",
    "Archive": "Archive",
    "DeletedItems": "DeletedItems",
    "JunkEmail": "JunkEmail",
}

# Cache for custom folder IDs resolved via Graph API (stable for process lifetime)
_resolved_folder_ids: dict[str, str] = {}


def _resolve_folder_id(client: GraphClient, folder: str) -> str:
    """Resolve a folder display name to its Graph API folder ID.

    Well-known folders (Inbox, SentItems, etc.) use predefined IDs.
    Custom folders are resolved via Graph API query and cached for the
    process lifetime.
    """
    if folder in _FOLDER_IDS:
        return _FOLDER_IDS[folder]

    if folder in _resolved_folder_ids:
        return _resolved_folder_ids[folder]

    data = client.get(
        "/me/mailFolders",
        {"$filter": f"displayName eq '{folder}'", "$select": "id,displayName", "$top": "1"},
    )
    folders = data.get("value", [])

    if not folders:
        raise GraphApiError(
            f"Mail folder not found: '{folder}'. "
            "Check the folder name in Outlook (case-sensitive, top-level folders only)."
        )

    folder_id = folders[0]["id"]
    _resolved_folder_ids[folder] = folder_id
    log.info("email.folder_resolved", display_name=folder, folder_id=folder_id[:20])
    return folder_id


def run(
    client: GraphClient,
    storage: StorageBackend,
    state: dict,
    config: EmailExtractorConfig,
    converters_config: dict,
) -> tuple[dict, int]:
    """Extract emails from configured folders using delta queries.

    Returns (updated_state, total_items_written).
    """
    total_written = 0
    seen_keys: set[tuple[str, str]] = set()

    for folder in config.folders:
        folder_key = f"delta_link_{folder}"
        delta_link = state.get(folder_key)

        items, new_delta_link = _sync_folder(
            client,
            storage,
            folder,
            delta_link,
            config,
            converters_config,
            seen_keys,
        )

        if new_delta_link:
            state[folder_key] = new_delta_link

        total_written += items

    state["last_sync"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    log.info("email.sync_complete", total_written=total_written)
    return state, total_written


def _sync_folder(
    client: GraphClient,
    storage: StorageBackend,
    folder: str,
    delta_link: str | None,
    config: EmailExtractorConfig,
    converters_config: dict,
    seen_keys: set[tuple[str, str]],
) -> tuple[int, str | None]:
    """Sync a single mail folder. Returns (items_written, new_delta_link)."""
    folder_id = _resolve_folder_id(client, folder)
    path = f"/me/mailFolders/{folder_id}/messages/delta"

    sync_type = "incremental" if delta_link else "initial"
    log.info("email.folder_sync_start", folder=folder, sync_type=sync_type)

    params = {
        "$select": "id,subject,bodyPreview,body,from,toRecipients,ccRecipients,"
        "receivedDateTime,importance,hasAttachments,webLink,parentFolderId",
        "$top": "50",
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

    messages, new_delta_link = client.get_delta(path, delta_link, params=params, max_pages=client.max_pages)

    written = 0
    for msg in messages[: config.max_items_per_sync]:
        if _write_email(storage, client, msg, folder, config, converters_config, seen_keys):
            written += 1

    log.info("email.folder_synced", folder=folder, sync_type=sync_type, fetched=len(messages), written=written)
    return written, new_delta_link


def _write_email(
    storage: StorageBackend,
    client: GraphClient,
    msg: dict,
    folder: str,
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

    # Dedup: skip same (minute, slug) pair within this sync run
    slug = slugify(subject, 80)
    key = (received[:16], slug)
    if key in seen_keys:
        log.info("email.skipped_duplicate", slug=slug, received=received[:16])
        return False
    seen_keys.add(key)

    # Extract sender
    from_field = (msg.get("from") or {}).get("emailAddress", {})
    sender_address = from_field.get("address", "")
    sender_name = from_field.get("name", "")

    # Extract recipients
    to_recipients = [r.get("emailAddress", {}).get("address", "") for r in msg.get("toRecipients", [])]

    # Convert body
    body_obj = msg.get("body", {})
    content_type = body_obj.get("contentType", "text")
    raw_body = body_obj.get("content", "")

    if content_type == "html":
        body_md = html_to_markdown(raw_body)
    else:
        body_md = raw_body

    # Build frontmatter
    fm = build_email_frontmatter(
        subject=subject,
        message_id=message_id,
        received_time=received,
        folder=folder,
        sender_address=sender_address,
        sender_name=sender_name,
        to_recipients=to_recipients,
        importance=msg.get("importance", "normal"),
        has_attachments=msg.get("hasAttachments", False),
        web_link=msg.get("webLink", ""),
    )

    # Build body
    body_parts = [f"# {subject}\n"]
    body_parts.append(f"**From:** {sender_name} <{sender_address}>")
    if to_recipients:
        body_parts.append(f"**To:** {', '.join(to_recipients)}")
    body_parts.append(f"**Date:** {received}\n")
    body_parts.append("---\n")
    body_parts.append(body_md)

    content = dumps_markdown(fm, "\n".join(body_parts))

    # Determine file path: emails/{year}/{date}/{slug}/index.md
    date_str = received[:10]
    year = date_str[:4]
    hsh = short_hash(message_id, 6)
    email_dir = f"emails/{year}/{date_str}/{slug}-{hsh}"
    file_path = f"{email_dir}/index.md"

    storage.write_file(file_path, content)

    if config.download_attachments and msg.get("hasAttachments"):
        _download_attachments(client, storage, message_id, email_dir, config, converters_config)

    return True
