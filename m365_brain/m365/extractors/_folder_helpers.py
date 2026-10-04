"""Mail folder resolution and discovery helpers for the email extractor."""

from __future__ import annotations

import structlog

from m365_brain.m365.client import GraphApiError, GraphClient

log = structlog.get_logger()

# Well-known folder names Graph accepts in place of a folder ID, for both /me and
# /users/{address}. They are aliases, so they are resolved to the real ID: sync
# state is keyed by ID, and `Inbox` and a discovered `Posteingang` are one folder.
WELL_KNOWN_FOLDERS = frozenset({"Inbox", "SentItems", "Drafts", "Archive", "DeletedItems", "JunkEmail"})

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
    # German (de-DE) mailbox locale, as Graph returns it
    "Entwürfe",
    "Gelöschte Elemente",
    "Junk-E-Mail",
    "Postausgang",
    "Verlauf der Unterhaltung",
    "Synchronisierungsprobleme",
    "Erneut erinnern aktiviert",
    "Geplant",
    "RSS-Feeds",
    "RSS-Abonnements",
}


def resolve_folder_id(client: GraphClient, endpoint_base: str, address: str, folder: str) -> str:
    """Resolve a configured folder name to its Graph folder ID for a mailbox.

    A well-known alias (Inbox, SentItems, ...) is fetched by name; any other name
    is looked up by display name among the root's children.
    """
    if folder in WELL_KNOWN_FOLDERS:
        folder_id = client.get(f"{endpoint_base}/mailFolders/{folder}", {"$select": "id,displayName"})["id"]
    else:
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


def _is_discoverable_folder(folder: dict) -> tuple[str, str] | None:
    """Return (display_name, folder_id) if the folder should be discovered, else None."""
    display = folder.get("displayName") or ""
    folder_id = folder.get("id") or ""
    if not display or not folder_id:
        return None
    if folder.get("isHidden", False) or display in AUTO_DISCOVER_SKIP_DISPLAY:
        return None
    return display, folder_id


def list_all_folders(client: GraphClient, endpoint_base: str, address: str) -> tuple[list[tuple[str, str]], bool]:
    """Every visible mail folder, at any depth, for auto-discovery.

    Returns ((display_name, folder_id) tuples, truncated). System / noise folders
    are filtered out by display name and the `isHidden` flag. Display names may
    repeat across the tree; sync state is keyed by ID, so that is harmless.
    `truncated` is True when any page walk hit `graph.max_pages`, so the list
    may be missing folders.

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
    any_truncated = False
    pending = [f"{endpoint_base}/mailFolders"]
    while pending:
        folders, truncated = client.get_pages(pending.pop(), {"$select": FOLDER_SELECT}, client.max_pages)
        any_truncated = any_truncated or truncated
        if truncated:
            log.warning("email.folder_discovery_truncated", mailbox=address, max_pages=client.max_pages)
        for f in folders:
            discovered = _is_discoverable_folder(f)
            if discovered is None:
                continue
            display, folder_id = discovered
            result.append((display, folder_id))
            if f.get("childFolderCount", 0):
                pending.append(f"{endpoint_base}/mailFolders/{folder_id}/childFolders")
    log.info("email.folders_discovered", mailbox=address, count=len(result))
    return result, any_truncated
