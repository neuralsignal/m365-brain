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


FOLDER_SELECT = "id,displayName,isHidden,childFolderCount"
"""What discovery needs off a `mailFolder`.

`childFolderCount` is in here so the walk descends only where there is
something to descend into -- omit it from `$select` and Graph omits it from the
response, which reads as "no children" and silently restores the bug this
traversal exists to fix. `childFolders` is walked by request rather than
`$expand`ed: the expansion returns one level, so a nested tree still needs the
traversal and the expansion only makes the first page heavier."""


def list_all_folders(client: GraphClient, endpoint_base: str, address: str) -> list[tuple[str, str]]:
    """Every visible mail folder, at any depth, for auto-discovery.

    Returns (display_name, folder_id) tuples with system / noise folders
    filtered out by display name and the `isHidden` flag. The caller can
    prime the resolved-id cache via `cache_folder_id` to avoid a second
    Graph round-trip per folder.

    **Two silent ceilings used to sit on these lines**, and `folders: null` is
    documented as "auto-discover all visible folders", so both were losses the
    operator had asked not to have:

    1. `GET /mailFolders` returns *only the root's children*. Microsoft's own
       reference says so outright -- "this operation doesn't return all mail
       folders in a mailbox, only the child folders of the root folder […] each
       child folder must be traversed separately". Anything an operator had
       filed one level down was never synced, never indexed, never triaged, and
       nothing said so.
    2. It was a single `client.get` with a literal `$top=100` and no
       `@odata.nextLink` follow, so a mailbox with more folders than that lost
       the tail without a warning. `get_pages` reports truncation; a bare `get`
       cannot.

    Both are fixed here: the collection is paged under `graph.max_pages`, and
    the walk descends into `childFolders`. A skipped folder is not descended
    into -- the children of `Deleted Items` are deleted items.

    Note: Graph API v1.0 does not expose `wellKnownName` on `mailFolder`, so
    filtering relies on `displayName` plus `isHidden`. See
    `AUTO_DISCOVER_SKIP_DISPLAY` for the localized display-name list.
    """
    result: list[tuple[str, str]] = []
    pending = [f"{endpoint_base}/mailFolders"]
    while pending:
        folders, truncated = client.get_pages(pending.pop(), {"$select": FOLDER_SELECT}, client.max_pages)
        if truncated:
            log.warning("email.folder_discovery_truncated", mailbox=address, max_pages=client.max_pages)
        for f in folders:
            display = f.get("displayName") or ""
            folder_id = f.get("id") or ""
            if not display or not folder_id:
                continue
            if f.get("isHidden", False) or display in AUTO_DISCOVER_SKIP_DISPLAY:
                continue
            result.append((display, folder_id))
            if f.get("childFolderCount", 0):
                pending.append(f"{endpoint_base}/mailFolders/{folder_id}/childFolders")
    log.info("email.folders_discovered", mailbox=address, count=len(result))
    return result


def cache_folder_id(folder_cache: dict[tuple[str, str], str], address: str, display: str, folder_id: str) -> None:
    """Insert a (address, display) → folder_id entry into the caller-owned cache."""
    folder_cache[(address, display)] = folder_id
