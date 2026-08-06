"""Email extractor — syncs emails via Graph API delta queries.

Reads from {/me | /users/{address}}/mailFolders/{folder}/messages/delta for each
configured (mailbox, folder) pair. Writes Obsidian-compatible markdown files
with YAML frontmatter, namespaced under emails/{output_subdir}/ when set.
Downloads and optionally converts email attachments.

**No `lookback_days`, and the reason it went is only half known.** Settled: the
`receivedDateTime ge <cutoff>` filter had no effect -- a cleared initial sync
wrote 1,062 pre-cutoff messages across all seven folders. Not settled: *why*.
The claim this code used to carry, "a message delta does not support $filter",
is wrong; Microsoft documents `receivedDateTime ge|gt` as one of the two
supported expressions, which is the one the removed code sent. So Graph ignores
it, or our filter string or param plumbing was wrong, and nothing offline tells
those apart. It stays gone -- a knob that lies is worse than no knob -- but
restoring it needs a measured round, not a doc page. Restored, it brings a
second trap: `$filter` caps a delta round at 5,000 messages, arriving with a
deltaLink as if the folder were complete.
"""

from __future__ import annotations

from datetime import UTC, datetime

import structlog

from m365_brain.config import EmailExtractorConfig, MailboxConfig
from m365_brain.m365.client import GraphClient
from m365_brain.m365.converters.html_to_md import html_to_markdown
from m365_brain.m365.extractors._attachment_helpers import download_attachments
from m365_brain.m365.extractors._folder_helpers import (
    FOLDER_IDS,
    cache_folder_id,
    list_all_folders,
    resolve_folder_id,
)
from m365_brain.m365.extractors.base import ExtractorContext
from m365_brain.m365.frontmatter import EmailData, build_email_frontmatter
from m365_brain.m365.markdown_writer import dumps_markdown, short_hash, slugify
from m365_brain.storage.base import StorageBackend
from m365_brain.vault.removal import PATH_MAP_STATE_KEY

log = structlog.get_logger()

name = "email"
required_scopes = ["Mail.Read"]

# Sentinel meaning "the authenticated user's mailbox"; uses /me/* endpoints.
_ME = "me"


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
    ctx: ExtractorContext,
) -> tuple[dict, int]:
    """Extract emails from every (mailbox, folder) pair using delta queries.

    Returns (updated_state, total_items_written).
    """
    total_written = 0
    seen_keys: set[tuple[str, str]] = set()
    folder_cache: dict[tuple[str, str], str] = {}
    path_map: dict[str, str] = state.setdefault(PATH_MAP_STATE_KEY, {})

    for mailbox in config.mailboxes:
        folders = _folders_for_mailbox(client, mailbox, folder_cache)

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
                ctx,
                seen_keys,
                folder_cache,
                path_map,
            )

            if new_delta_link:
                state[state_key] = new_delta_link

            total_written += items

    state["last_sync"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    log.info("email.sync_complete", total_written=total_written)
    return state, total_written


def _folders_for_mailbox(
    client: GraphClient,
    mailbox: MailboxConfig,
    folder_cache: dict[tuple[str, str], str],
) -> list[str]:
    """Resolve the folder display-name list for a mailbox.

    If `mailbox.folders` is None, auto-discover via Graph API (and prime the
    folder-ID cache). Otherwise return the configured list verbatim.
    """
    if mailbox.folders is not None:
        return list(mailbox.folders)
    discovered = list_all_folders(client, _endpoint_base(mailbox.address), mailbox.address)
    for display, folder_id in discovered:
        if display not in FOLDER_IDS:
            cache_folder_id(folder_cache, mailbox.address, display, folder_id)
    return [display for display, _ in discovered]


def _sync_folder(
    client: GraphClient,
    storage: StorageBackend,
    address: str,
    output_subdir: str,
    folder: str,
    delta_link: str | None,
    config: EmailExtractorConfig,
    ctx: ExtractorContext,
    seen_keys: set[tuple[str, str]],
    folder_cache: dict[tuple[str, str], str],
    path_map: dict[str, str],
) -> tuple[int, str | None]:
    """Sync a single (mailbox, folder). Returns (items_written, new_delta_link)."""
    endpoint_base = _endpoint_base(address)
    folder_id = resolve_folder_id(client, endpoint_base, address, folder, folder_cache)
    path = f"{endpoint_base}/mailFolders/{folder_id}/messages/delta"

    sync_type = "incremental" if delta_link else "initial"
    log.info("email.folder_sync_start", mailbox=address, folder=folder, sync_type=sync_type)

    params = {
        "$select": "id,conversationId,subject,bodyPreview,body,from,toRecipients,ccRecipients,"
        "receivedDateTime,importance,hasAttachments,webLink,parentFolderId",
        # $top on a delta query caps the WHOLE enumeration, not the page: Graph
        # returns at most this many messages across every page of the round and
        # then closes with a deltaLink, so anything past the cap is never
        # fetched and never resumed. It is the item budget, and only the
        # configured budget may set it. The constant 50 that used to sit here
        # made every initial folder sync stop at 50 messages, ok=True.
        "$top": str(config.max_items_per_sync),
    }
    # No $filter, and no time window: see the module docstring for what is
    # settled about `lookback_days` and what is not.

    # Graph pages a delta round at its own size (~10 items) whatever $top says,
    # so a page budget derived from the item budget could never bind. $top bounds
    # the items server-side; the page walk needs only the global runaway bound,
    # and a round that bound interrupts resumes from the pending nextLink next
    # cycle. Everything fetched IS processed — slicing after the fetch would
    # skip the tail forever once the (resume) delta link is persisted.
    messages, new_delta_link = client.get_delta(path, delta_link, params=params, max_pages=client.max_pages)

    written = 0
    for msg in messages:
        # The delta endpoint has always emitted @removed; nothing read it, so a
        # deleted mail stayed in the vault forever.
        if "@removed" in msg:
            ctx.removal.remove(extractor=name, upstream_id=msg.get("id", ""), path_map=path_map)
            continue
        if _write_email(
            storage,
            client,
            msg,
            folder,
            address,
            output_subdir,
            config,
            ctx,
            seen_keys,
            path_map,
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
    email_dir = ctx.paths.inbox_item(name, *segments)
    file_path = ctx.paths.entry_file(email_dir)

    storage.write_file(file_path, content)
    # The directory, not the entry file: attachments and their conversions
    # live beside it and must be removed with it.
    path_map[message_id] = email_dir

    if config.download_attachments and msg.get("hasAttachments"):
        download_attachments(
            client,
            storage,
            _endpoint_base(address),
            message_id,
            email_dir,
            config,
            ctx,
        )

    return True
