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

from dataclasses import dataclass, field
from datetime import UTC, datetime

import structlog

from m365_brain.config import EmailExtractorConfig, MailboxConfig
from m365_brain.m365.client import GraphClient
from m365_brain.m365.extractors._email_writer import EmailSyncContext, write_email
from m365_brain.m365.extractors._folder_helpers import (
    WELL_KNOWN_FOLDERS,
    list_all_folders,
    resolve_folder_id,
)
from m365_brain.m365.extractors.base import ExtractorContext
from m365_brain.storage.base import StorageBackend
from m365_brain.vault.removal import PATH_MAP_STATE_KEY

log = structlog.get_logger()

name = "email"
required_scopes = ["Mail.Read"]

# Sentinel meaning "the authenticated user's mailbox"; uses /me/* endpoints.
_ME = "me"


@dataclass
class _SyncState:
    seen_keys: set[tuple[str, str]] = field(default_factory=set)
    path_map: dict[str, str] = field(default_factory=dict)


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
    ss = _SyncState(path_map=state.setdefault(PATH_MAP_STATE_KEY, {}))
    sync_ctx = EmailSyncContext(
        storage=storage,
        client=client,
        config=config,
        ctx=ctx,
        seen_keys=ss.seen_keys,
        path_map=ss.path_map,
    )

    for mailbox in config.mailboxes:
        folders = _folders_for_mailbox(client, mailbox)
        adopt_name_keyed_delta_links(state, mailbox.address, folders, _legacy_alias_ids(client, state, mailbox.address))

        for folder, folder_id in folders:
            state_key = delta_state_key(mailbox.address, folder_id)
            delta_link = state.get(state_key)

            items, new_delta_link = _sync_folder(
                sync_ctx,
                mailbox.address,
                mailbox.output_subdir,
                folder,
                folder_id,
                delta_link,
                ss,
            )

            if new_delta_link:
                state[state_key] = new_delta_link

            total_written += items

    state["last_sync"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    log.info("email.sync_complete", total_written=total_written)
    return state, total_written


def delta_state_key(address: str, folder_id: str) -> str:
    """The state key holding a folder's delta link: by Graph ID, stable across rename and move."""
    return f"delta_link_{address}_id_{folder_id}"


def _name_state_key(address: str, folder: str) -> str:
    """The pre-#392 key, by display name or alias. Read only to migrate it."""
    return f"delta_link_{address}_{folder}"


def _legacy_alias_ids(client: GraphClient, state: dict, address: str) -> dict[str, str]:
    """Real IDs of the well-known aliases that still own a name-keyed delta link."""
    endpoint_base = _endpoint_base(address)
    return {
        alias: resolve_folder_id(client, endpoint_base, address, alias)
        for alias in sorted(WELL_KNOWN_FOLDERS)
        if _name_state_key(address, alias) in state
    }


def adopt_name_keyed_delta_links(
    state: dict,
    address: str,
    folders: list[tuple[str, str]],
    alias_ids: dict[str, str],
) -> None:
    """Move delta links stored under a folder's name onto its ID key, so no folder re-pulls.

    A folder's old keys are its display name and every well-known alias that
    resolves to it. Two of them (`Inbox` and `Posteingang`) keep the one under the
    current display name, else the alphabetically first. An ID key that already
    exists wins over all of them. Every old key is removed, so a second call is a
    no-op.
    """
    for display, folder_id in folders:
        names = {display} | {alias for alias, alias_id in alias_ids.items() if alias_id == folder_id}
        old = sorted(n for n in names if _name_state_key(address, n) in state)
        if not old:
            continue
        id_key = delta_state_key(address, folder_id)
        if id_key not in state:
            kept = display if display in old else old[0]
            state[id_key] = state[_name_state_key(address, kept)]
            log.info(
                "email.delta_link_migrated",
                mailbox=address,
                folder=display,
                from_key=_name_state_key(address, kept),
                to_key=id_key,
            )
        else:
            kept = None
        for name in old:
            del state[_name_state_key(address, name)]
            if name != kept:
                log.warning(
                    "email.delta_link_discarded",
                    mailbox=address,
                    folder=display,
                    key=_name_state_key(address, name),
                    kept_key=id_key,
                )


def _folders_for_mailbox(client: GraphClient, mailbox: MailboxConfig) -> list[tuple[str, str]]:
    """(display name, folder ID) for every folder of a mailbox.

    If `mailbox.folders` is None, auto-discover via Graph API. Otherwise resolve
    the configured names, which stay the display name.
    """
    endpoint_base = _endpoint_base(mailbox.address)
    if mailbox.folders is None:
        return list_all_folders(client, endpoint_base, mailbox.address)
    return [(folder, resolve_folder_id(client, endpoint_base, mailbox.address, folder)) for folder in mailbox.folders]


def _sync_folder(
    sync_ctx: EmailSyncContext,
    address: str,
    output_subdir: str,
    folder: str,
    folder_id: str,
    delta_link: str | None,
    ss: _SyncState,
) -> tuple[int, str | None]:
    """Sync a single (mailbox, folder). Returns (items_written, new_delta_link)."""
    endpoint_base = _endpoint_base(address)
    path = f"{endpoint_base}/mailFolders/{folder_id}/messages/delta"

    sync_type = "incremental" if delta_link else "initial"
    log.info("email.folder_sync_start", mailbox=address, folder=folder, sync_type=sync_type)

    params = {
        "$select": "id,conversationId,subject,bodyPreview,body,from,toRecipients,ccRecipients,"
        "receivedDateTime,importance,hasAttachments,webLink,parentFolderId",
        "$top": str(sync_ctx.config.max_items_per_sync),
    }

    messages, new_delta_link = sync_ctx.client.get_delta(
        path, delta_link, params=params, max_pages=sync_ctx.client.max_pages
    )

    written = 0
    for msg in messages:
        if "@removed" in msg:
            sync_ctx.ctx.removal.remove(extractor=name, upstream_id=msg.get("id", ""), path_map=ss.path_map)
            continue
        if write_email(
            sync_ctx,
            msg,
            folder,
            address,
            output_subdir,
            endpoint_base,
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
