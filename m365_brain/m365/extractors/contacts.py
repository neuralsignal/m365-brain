"""Contacts extractor — syncs personal contacts via Graph API delta queries.

Reads from /me/contacts/delta (and optionally /me/contactFolders).
Writes Obsidian-compatible markdown files with YAML frontmatter.

**There is no item budget here, and there is nowhere to put one.** This module
used to carry `ceil(max_items_per_sync / 10)` as its page budget, where the 10
was a module constant guessing Graph's server-side page size for a personal
contacts collection. Nothing measured it, and the derivation was load-bearing:
a real page size of 5 halved the budget, a real 20 doubled it, and either way
the round stopped somewhere the operator did not ask for. The `$top` that
carries an item budget on the message delta (#264) has no documented
counterpart on this endpoint, and the library sends no
`Prefer: odata.maxpagesize` anywhere -- so no channel existed by which an item
budget could reach the server at all. A bound that cannot bind is not a
safeguard, and one derived from a guess is worse than none, because it reports
a number.

`graph.max_pages` bounds the walk instead, which is what it is for, and a round
it interrupts resumes from the pending nextLink next cycle. This is the
treatment `directory.py` got in #264 for the same shape.
"""

from __future__ import annotations

from datetime import UTC, datetime

import structlog

from m365_brain.config import ContactsExtractorConfig
from m365_brain.m365.client import GraphClient
from m365_brain.m365.extractors.base import ExtractorContext
from m365_brain.m365.frontmatter import ContactData, address_observations, build_contact_frontmatter
from m365_brain.m365.markdown_writer import dumps_markdown, short_hash, slugify
from m365_brain.storage.base import StorageBackend
from m365_brain.vault.removal import PATH_MAP_STATE_KEY

log = structlog.get_logger()

name = "contacts"
required_scopes = ["Contacts.Read"]


def run(
    client: GraphClient,
    storage: StorageBackend,
    state: dict,
    config: ContactsExtractorConfig,
    ctx: ExtractorContext,
) -> tuple[dict, int]:
    """Extract contacts using delta queries.

    Returns (updated_state, total_items_written).
    """
    total_written = 0
    path_map: dict[str, str] = state.setdefault(PATH_MAP_STATE_KEY, {})

    # Sync default contacts folder
    delta_link = state.get("delta_link")
    written, new_delta_link = _sync_contacts(
        client,
        storage,
        "/me/contacts/delta",
        delta_link,
        ctx,
        path_map,
    )
    if new_delta_link:
        state["delta_link"] = new_delta_link
    total_written += written

    # Sync contact sub-folders if enabled
    if config.include_contact_folders:
        folder_written = _sync_contact_folders(client, storage, state, ctx, path_map)
        total_written += folder_written

    state["last_sync"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    log.info("contacts.sync_complete", total_written=total_written)
    return state, total_written


def _sync_contact_folders(
    client: GraphClient,
    storage: StorageBackend,
    state: dict,
    ctx: ExtractorContext,
    path_map: dict[str, str],
) -> int:
    """Sync contacts from all contact sub-folders."""
    folders = list(
        client.get_paginated("/me/contactFolders", params={"$select": "id,displayName"}, max_pages=client.max_pages)
    )
    written = 0

    for folder in folders:
        folder_id = folder.get("id", "")
        if not folder_id:
            continue

        folder_key = f"delta_link_folder_{folder_id}"
        delta_link = state.get(folder_key)
        path = f"/me/contactFolders/{folder_id}/contacts/delta"

        folder_written, new_delta_link = _sync_contacts(
            client,
            storage,
            path,
            delta_link,
            ctx,
            path_map,
        )

        if new_delta_link:
            state[folder_key] = new_delta_link
        written += folder_written

    return written


def _sync_contacts(
    client: GraphClient,
    storage: StorageBackend,
    path: str,
    delta_link: str | None,
    ctx: ExtractorContext,
    path_map: dict[str, str],
) -> tuple[int, str | None]:
    """Sync contacts from a single delta endpoint. Returns (items_written, new_delta_link)."""
    # `graph.max_pages` is the only bound -- see the module docstring for why
    # there is no item budget. Everything fetched IS processed; slicing after
    # the fetch would skip the tail forever once the (resume) delta link is
    # persisted.
    contacts, new_delta_link = client.get_delta(path, delta_link, params=None, max_pages=client.max_pages)

    written = 0
    for contact in contacts:
        # A delta feed emits @removed for a deleted contact. The design doc
        # recorded contacts as having "no upstream removal signal"; that is
        # wrong -- this is a delta endpoint like email's, and without the
        # branch a deleted contact stays in the vault forever. An @removed
        # entry also carries no displayName, so it would otherwise be
        # dropped by _extract_contact_data as invalid, silently.
        if "@removed" in contact:
            ctx.removal.remove(extractor=name, upstream_id=contact.get("id", ""), path_map=path_map)
            continue
        extracted = _extract_contact_data(contact)
        if extracted is None:
            continue
        contact_data, notes = extracted
        if _write_contact(storage, contact_data, notes, ctx, path_map):
            written += 1

    log.info("contacts.batch_synced", fetched=len(contacts), written=written)
    return written, new_delta_link


def _extract_phones(contact: dict) -> list[str]:
    """Extract all phone numbers from a contact."""
    phones = []
    for phone in contact.get("businessPhones", []):
        if phone:
            phones.append(phone)
    mobile = contact.get("mobilePhone")
    if mobile:
        phones.append(mobile)
    return phones


def _extract_emails(contact: dict) -> list[str]:
    """Extract email addresses from a contact."""
    return [e.get("address", "") for e in contact.get("emailAddresses", []) if e.get("address")]


def _extract_contact_data(contact: dict) -> tuple[ContactData, str] | None:
    """Extract and normalize contact data. Returns None if invalid.

    Personal notes are returned separately because they belong in the body, not
    the frontmatter.
    """
    contact_id = contact.get("id", "")
    display_name = contact.get("displayName") or ""

    if not contact_id or not display_name:
        log.warning("contacts.skipping_invalid", contact_id=contact_id)
        return None

    data = ContactData(
        display_name=display_name,
        contact_id=contact_id,
        email_addresses=_extract_emails(contact),
        phones=_extract_phones(contact),
        company=contact.get("companyName") or "",
        job_title=contact.get("jobTitle") or "",
        department=contact.get("department") or "",
        categories=contact.get("categories") or [],
    )
    return data, contact.get("personalNotes") or ""


def _write_contact(
    storage: StorageBackend,
    data: ContactData,
    notes: str,
    ctx: ExtractorContext,
    path_map: dict[str, str],
) -> bool:
    """Build frontmatter and markdown body for a contact, then write to storage."""
    fm = build_contact_frontmatter(data)

    body_parts = [f"# {data.display_name}\n", "## Details\n"]

    # Observation lines, not prose: `- **Email:** a@example.com` carries no
    # [category] and no #tag, so it parsed as nothing and the address was as
    # unreadable from the body as it was from the list in the frontmatter.
    body_parts.extend(address_observations(data))
    if data.phones:
        for phone in data.phones:
            body_parts.append(f"- **Phone:** {phone}")
    if data.company:
        body_parts.append(f"- **Company:** {data.company}")
    if data.job_title:
        body_parts.append(f"- **Title:** {data.job_title}")
    if data.department:
        body_parts.append(f"- **Department:** {data.department}")

    if notes:
        body_parts.append("\n## Notes\n")
        body_parts.append(notes)

    content = dumps_markdown(fm, "\n".join(body_parts))

    slug = slugify(data.display_name, 80)
    hsh = short_hash(data.contact_id, 6)
    item_dir = ctx.paths.inbox_item(name, f"{slug}-{hsh}")
    file_path = ctx.paths.entry_file(item_dir)

    storage.write_file(file_path, content)
    # The directory, not the entry file -- see email.py.
    path_map[data.contact_id] = item_dir
    return True
