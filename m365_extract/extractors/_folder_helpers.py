"""Mail folder resolution and discovery helpers for the email extractor."""

from __future__ import annotations

import structlog

from m365_extract.graph_client import GraphApiError, GraphClient

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

# Well-known folder names to skip during folder auto-discovery (mailbox-system
# noise that should not be ingested as content).
AUTO_DISCOVER_SKIP_WELL_KNOWN = {
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
AUTO_DISCOVER_SKIP_DISPLAY = {
    "Conversation Action Settings",
    "Quick Step Settings",
    "RSS Feeds",
    "RSS Subscriptions",
    "Yammer Root",
    "Files",
    "Junk E-Mail",
}

# Process-lifetime cache of resolved folder IDs, keyed by (address, display_name).
_resolved_folder_ids: dict[tuple[str, str], str] = {}


def resolve_folder_id(client: GraphClient, endpoint_base: str, address: str, folder: str) -> str:
    """Resolve a folder display name to its Graph API folder ID for a mailbox.

    Well-known folders (Inbox, SentItems, etc.) use predefined IDs. Custom
    folders are resolved via Graph API query and cached for the process
    lifetime, keyed by (address, folder).
    """
    if folder in FOLDER_IDS:
        return FOLDER_IDS[folder]

    cache_key = (address, folder)
    if cache_key in _resolved_folder_ids:
        return _resolved_folder_ids[cache_key]

    data = client.get(
        f"{endpoint_base}/mailFolders",
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


def list_all_folders(client: GraphClient, endpoint_base: str, address: str) -> list[tuple[str, str]]:
    """List all top-level mail folders for auto-discovery.

    Returns (display_name, folder_id) tuples with system / noise folders
    filtered out. The caller can prime the resolved-id cache via
    `cache_folder_id` to avoid a second Graph round-trip per folder.
    """
    data = client.get(
        f"{endpoint_base}/mailFolders",
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
        if well_known in AUTO_DISCOVER_SKIP_WELL_KNOWN:
            continue
        if display in AUTO_DISCOVER_SKIP_DISPLAY:
            continue
        result.append((display, folder_id))
    log.info("email.folders_discovered", mailbox=address, count=len(result))
    return result


def cache_folder_id(address: str, display: str, folder_id: str) -> None:
    """Insert a (address, display) → folder_id entry into the resolved-id cache."""
    _resolved_folder_ids[(address, display)] = folder_id
