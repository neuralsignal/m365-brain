"""Contacts extractor — syncs personal contacts via Graph API delta queries.

Reads from /me/contacts/delta (and optionally /me/contactFolders).
Writes Obsidian-compatible markdown files with YAML frontmatter.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime

import structlog

from m365_brain.config import ContactsExtractorConfig
from m365_brain.frontmatter import ContactData, build_contact_frontmatter
from m365_brain.graph_client import GraphClient
from m365_brain.markdown_writer import dumps_markdown, short_hash, slugify
from m365_brain.storage.base import StorageBackend

log = structlog.get_logger()

name = "contacts"
required_scopes = ["Contacts.Read"]

_CONTACT_SELECT = (
    "id,displayName,givenName,surname,emailAddresses,businessPhones,"
    "mobilePhone,companyName,jobTitle,department,officeLocation,"
    "businessAddress,homeAddress,personalNotes,birthday,categories,"
    "createdDateTime,lastModifiedDateTime"
)

# Graph's documented default page size for personal-contact collections; the
# contacts delta endpoint rejects $top, so the page budget assumes this server
# default: ceil(max_items_per_sync / page size) pages per cycle. A round
# interrupted by the budget resumes from the pending nextLink next cycle.
_DELTA_DEFAULT_PAGE_SIZE = 10


def run(
    client: GraphClient,
    storage: StorageBackend,
    state: dict,
    config: ContactsExtractorConfig,
) -> tuple[dict, int]:
    """Extract contacts using delta queries.

    Returns (updated_state, total_items_written).
    """
    total_written = 0

    # Sync default contacts folder
    delta_link = state.get("delta_link")
    written, new_delta_link = _sync_contacts(
        client,
        storage,
        "/me/contacts/delta",
        delta_link,
        config.max_items_per_sync,
    )
    if new_delta_link:
        state["delta_link"] = new_delta_link
    total_written += written

    # Sync contact sub-folders if enabled
    if config.include_contact_folders:
        folder_written = _sync_contact_folders(client, storage, state, config.max_items_per_sync)
        total_written += folder_written

    state["last_sync"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    log.info("contacts.sync_complete", total_written=total_written)
    return state, total_written


def _sync_contact_folders(
    client: GraphClient,
    storage: StorageBackend,
    state: dict,
    max_items: int,
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
            max_items,
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
    max_items: int,
) -> tuple[int, str | None]:
    """Sync contacts from a single delta endpoint. Returns (items_written, new_delta_link)."""
    # Contacts delta endpoint rejects $select, $top, $filter, etc. The page
    # budget bounds per-cycle work; everything fetched IS processed — slicing
    # after the fetch would skip the tail forever once the (resume) delta link
    # is persisted.
    max_pages = max(1, math.ceil(max_items / _DELTA_DEFAULT_PAGE_SIZE))
    contacts, new_delta_link = client.get_delta(path, delta_link, params=None, max_pages=max_pages)

    written = 0
    for contact in contacts:
        extracted = _extract_contact_data(contact)
        if extracted is None:
            continue
        contact_data, notes = extracted
        if _write_contact(storage, contact_data, notes):
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


def _write_contact(storage: StorageBackend, data: ContactData, notes: str) -> bool:
    """Build frontmatter and markdown body for a contact, then write to storage."""
    fm = build_contact_frontmatter(data)

    body_parts = [f"# {data.display_name}\n", "## Details\n"]

    if data.email_addresses:
        for addr in data.email_addresses:
            body_parts.append(f"- **Email:** {addr}")
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
    file_path = f"contacts/{slug}-{hsh}/index.md"

    storage.write_file(file_path, content)
    return True
