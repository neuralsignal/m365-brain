"""Email extractor — syncs emails via Graph API delta queries.

Reads from {/me | /users/{address}}/mailFolders/{folder}/messages/delta for each
configured (mailbox, folder) pair. Writes Obsidian-compatible markdown files
with YAML frontmatter, namespaced under emails/{output_subdir}/ when set.
Downloads and optionally converts email attachments.
"""

from __future__ import annotations

import base64
import binascii
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import structlog

from m365_extract.config import EmailExtractorConfig, MailboxConfig
from m365_extract.converters.document import convert_document
from m365_extract.converters.html_to_md import html_to_markdown
from m365_extract.frontmatter import build_email_frontmatter
from m365_extract.graph_client import GraphApiError, GraphClient
from m365_extract.markdown_writer import dumps_markdown, short_hash, slugify
from m365_extract.storage.base import StorageBackend
from m365_extract.storage.exceptions import StorageError

log = structlog.get_logger()

name = "email"
required_scopes = ["Mail.Read"]

# Sentinel meaning "the authenticated user's mailbox"; uses /me/* endpoints.
_ME = "me"

# Graph API folder name → delta endpoint name mapping for well-known folders.
# These can be used in place of a folder ID for both /me and /users/{address}.
_FOLDER_IDS = {
    "Inbox": "Inbox",
    "SentItems": "SentItems",
    "Drafts": "Drafts",
    "Archive": "Archive",
    "DeletedItems": "DeletedItems",
    "JunkEmail": "JunkEmail",
}

# Well-known folder names to skip during folder auto-discovery (mailbox-system
# noise that should not be ingested as content).
_AUTO_DISCOVER_SKIP_WELL_KNOWN = {
    "drafts",
    "junkemail",
    "deleteditems",
    "syncissues",
    "outbox",
    "conflicts",
    "localfailures",
    "searchfolders",
    "serverfailures",
    "conversationhistory",
    "recoverableitemsdeletions",
    "scheduled",
}

# Display names to skip during folder auto-discovery (vendor or user-visible
# system folders without a wellKnownName).
_AUTO_DISCOVER_SKIP_DISPLAY = {
    "Conversation Action Settings",
    "Quick Step Settings",
    "RSS Feeds",
    "RSS Subscriptions",
    "Yammer Root",
    "Files",
    "Junk E-Mail",
}

# Cache for resolved folder IDs, keyed by (mailbox_address, folder_display_name).
# Stable for process lifetime.
_resolved_folder_ids: dict[tuple[str, str], str] = {}


def _endpoint_base(address: str) -> str:
    """Return the Graph API root path for a mailbox.

    `"me"` → `/me`; any other value is treated as a user UPN or object ID and
    yields `/users/{address}`.
    """
    if address == _ME:
        return "/me"
    return f"/users/{address}"


def _resolve_folder_id(client: GraphClient, address: str, folder: str) -> str:
    """Resolve a folder display name to its Graph API folder ID for a mailbox.

    Well-known folders (Inbox, SentItems, etc.) use predefined IDs. Custom
    folders are resolved via Graph API query and cached for the process
    lifetime, keyed by (address, folder).
    """
    if folder in _FOLDER_IDS:
        return _FOLDER_IDS[folder]

    cache_key = (address, folder)
    if cache_key in _resolved_folder_ids:
        return _resolved_folder_ids[cache_key]

    data = client.get(
        f"{_endpoint_base(address)}/mailFolders",
        {"$filter": f"displayName eq '{folder}'", "$select": "id,displayName", "$top": "1"},
    )
    folders = data.get("value", [])

    if not folders:
        raise GraphApiError(
            f"Mail folder not found: '{folder}' (mailbox={address}). "
            "Check the folder name in Outlook (case-sensitive, top-level folders only)."
        )

    folder_id = folders[0]["id"]
    _resolved_folder_ids[cache_key] = folder_id
    log.info("email.folder_resolved", mailbox=address, display_name=folder, folder_id=folder_id[:20])
    return folder_id


def _list_all_folders(client: GraphClient, address: str) -> list[tuple[str, str]]:
    """List all top-level mail folders for auto-discovery.

    Returns (display_name, folder_id) tuples with system / noise folders
    filtered out. The caller can prime `_resolved_folder_ids` with the IDs to
    avoid a second Graph round-trip per folder.
    """
    data = client.get(
        f"{_endpoint_base(address)}/mailFolders",
        {"$select": "id,displayName,wellKnownName", "$top": "100"},
    )
    folders = data.get("value", [])
    result: list[tuple[str, str]] = []
    for f in folders:
        well_known = (f.get("wellKnownName") or "").lower()
        display = f.get("displayName") or ""
        folder_id = f.get("id") or ""
        if not display or not folder_id:
            continue
        if well_known in _AUTO_DISCOVER_SKIP_WELL_KNOWN:
            continue
        if display in _AUTO_DISCOVER_SKIP_DISPLAY:
            continue
        result.append((display, folder_id))
    log.info("email.folders_discovered", mailbox=address, count=len(result))
    return result


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
    discovered = _list_all_folders(client, mailbox.address)
    for display, folder_id in discovered:
        if display not in _FOLDER_IDS:
            _resolved_folder_ids[(mailbox.address, display)] = folder_id
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
    folder_id = _resolve_folder_id(client, address, folder)
    path = f"{_endpoint_base(address)}/mailFolders/{folder_id}/messages/delta"

    sync_type = "incremental" if delta_link else "initial"
    log.info("email.folder_sync_start", mailbox=address, folder=folder, sync_type=sync_type)

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
        body_md = html_to_markdown(raw_body)
    else:
        body_md = raw_body

    fm = build_email_frontmatter(
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
        _download_attachments(client, storage, address, message_id, email_dir, config, converters_config)

    return True


def _download_attachments(
    client: GraphClient,
    storage: StorageBackend,
    address: str,
    message_id: str,
    email_dir: str,
    config: EmailExtractorConfig,
    converters_config: dict,
) -> None:
    """Download email attachments and optionally convert them to markdown."""
    path = f"{_endpoint_base(address)}/messages/{message_id}/attachments"
    params = {"$top": "20"}
    try:
        for att in client.get_paginated(path, params, max_pages=5):
            att_name = att.get("name", "")
            if not att_name or ":" in att_name:
                continue
            if att.get("isInline", False):
                continue
            size = att.get("size", 0)
            if size > config.max_attachment_size_mb * 1024 * 1024:
                log.warning("email.attachment_too_large", name=att_name, size_mb=size // (1024 * 1024))
                continue
            download_url = att.get("@microsoft.graph.downloadUrl")
            content_bytes_b64 = att.get("contentBytes")
            if not download_url and not content_bytes_b64:
                log.warning("email.attachment_no_download_url", name=att_name)
                continue
            try:
                if download_url:
                    data = client.get_bytes(download_url)
                else:
                    data = base64.b64decode(content_bytes_b64)
                storage.write_bytes(f"{email_dir}/attachments/{att_name}", data)
                ext = Path(att_name).suffix.lower()
                if ext in config.attachment_convert_extensions:
                    _convert_and_store(storage, data, att_name, email_dir, converters_config)
            except (GraphApiError, httpx.TransportError, binascii.Error, StorageError, OSError) as exc:
                log.warning("email.attachment_download_failed", name=att_name, error=str(exc))
    except (GraphApiError, httpx.TransportError) as exc:
        log.warning("email.attachments_fetch_failed", message_id=message_id, error=str(exc))


def _convert_and_store(
    storage: StorageBackend,
    data: bytes,
    att_name: str,
    email_dir: str,
    converters_config: dict,
) -> None:
    """Convert an attachment binary to markdown and write to storage."""
    suffix = Path(att_name).suffix
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp_path = Path(tmp.name)
            tmp_path.write_bytes(data)
        md_content = convert_document(tmp_path, converters_config)
        storage.write_file(f"{email_dir}/attachments_converted/{att_name}.md", md_content)
    except (OSError, ImportError, StorageError) as exc:
        log.warning("email.attachment_convert_failed", name=att_name, error=str(exc))
    finally:
        if tmp_path is not None and tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
