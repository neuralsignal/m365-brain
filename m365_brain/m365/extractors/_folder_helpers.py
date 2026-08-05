"""Mail folder resolution and discovery helpers for the email extractor."""

from __future__ import annotations

import structlog

from m365_brain.m365.client import GraphApiError, GraphClient

log = structlog.get_logger()

# Graph API folder name → delta endpoint name mapping for well-known folders.
# These can be used in place of a folder ID for both /me and /users/{address}.
FOLDER_IDS = {
    "Inbox": "Inbox",
    "SentItems": "SentItems",
    "Drafts": "Drafts",
    "Archive": "Archive",
    "DeletedItems": "DeletedItems",
    "JunkEmail": "JunkEmail",
}

# Display names to skip during folder auto-discovery.
#
# Graph API v1.0 does NOT expose `wellKnownName` on the mailFolder schema
# (it's beta-only), so the filter cannot match well-known IDs. Instead we
# match on the localized displayName that Graph actually returns for system
# folders + a few vendor/sync artefacts.
#
# The English set below covers the standard EN mailbox locale (Drafts, Sent
# Items, etc.). Add localized variants here when ingesting non-English
# mailboxes ("Entwürfe", "Gelöschte Elemente", "Posteingang"-friends, etc.).
AUTO_DISCOVER_SKIP_DISPLAY = {
    # System mailbox folders
    "Drafts",
    "Deleted Items",
    "Junk Email",
    "Junk E-Mail",
    "Outbox",
    "Conversation History",
    "Sync Issues",
    "Conflicts",
    "Local Failures",
    "Server Failures",
    "Recoverable Items",
    "Scheduled",
    # Vendor/system artefacts
    "Conversation Action Settings",
    "Quick Step Settings",
    "RSS Feeds",
    "RSS Subscriptions",
    "Yammer Root",
    "Files",
}


def resolve_folder_id(
    client: GraphClient,
    endpoint_base: str,
    address: str,
    folder: str,
    folder_cache: dict[tuple[str, str], str],
) -> str:
    """Resolve a folder display name to its Graph API folder ID for a mailbox.

    Well-known folders (Inbox, SentItems, etc.) use predefined IDs. Custom
    folders are resolved via Graph API query and cached in the caller-owned
    ``folder_cache``, keyed by (address, folder).
    """
    if folder in FOLDER_IDS:
        return FOLDER_IDS[folder]

    cache_key = (address, folder)
    if cache_key in folder_cache:
        return folder_cache[cache_key]

    safe_folder = folder.replace("'", "''")
    data = client.get(
        f"{endpoint_base}/mailFolders",
        {"$filter": f"displayName eq '{safe_folder}'", "$select": "id,displayName", "$top": "1"},
    )
    folders = data.get("value", [])

    if not folders:
        raise GraphApiError(
            f"Mail folder not found: '{folder}' (mailbox={address}). "
            "Check the folder name in Outlook (case-sensitive, top-level folders only).",
            None,
        )

    folder_id = folders[0]["id"]
    folder_cache[cache_key] = folder_id
    log.info("email.folder_resolved", mailbox=address, display_name=folder, folder_id=folder_id[:20])
    return folder_id


def list_all_folders(client: GraphClient, endpoint_base: str, address: str) -> list[tuple[str, str]]:
    """List all top-level mail folders for auto-discovery.

    Returns (display_name, folder_id) tuples with system / noise folders
    filtered out by display name and the `isHidden` flag. The caller can
    prime the resolved-id cache via `cache_folder_id` to avoid a second
    Graph round-trip per folder.

    Note: Graph API v1.0 does not expose `wellKnownName` on `mailFolder`, so
    filtering relies on `displayName` plus `isHidden`. See
    `AUTO_DISCOVER_SKIP_DISPLAY` for the localized display-name list.
    """
    data = client.get(
        f"{endpoint_base}/mailFolders",
        {"$select": "id,displayName,isHidden", "$top": "100"},
    )
    folders = data.get("value", [])
    result: list[tuple[str, str]] = []
    for f in folders:
        display = f.get("displayName") or ""
        folder_id = f.get("id") or ""
        if not display or not folder_id:
            continue
        if f.get("isHidden", False):
            continue
        if display in AUTO_DISCOVER_SKIP_DISPLAY:
            continue
        result.append((display, folder_id))
    log.info("email.folders_discovered", mailbox=address, count=len(result))
    return result


def cache_folder_id(folder_cache: dict[tuple[str, str], str], address: str, display: str, folder_id: str) -> None:
    """Insert a (address, display) → folder_id entry into the caller-owned cache."""
    folder_cache[(address, display)] = folder_id
