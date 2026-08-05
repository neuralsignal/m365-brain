"""Channel-specific ingest logic — fetch and convert message chains.

Analogous to ``_teams_ingest.py`` (shared chat/channel conversion), this
module holds the channel-only fetch-and-convert pipeline: watermark-based
early-stop paging of threaded chains and the per-chain StoredMessage
conversion loop. Extracted from ``teams_channels.py`` to keep both files
well under the 300-line limit.
"""

from __future__ import annotations

import structlog

from m365_brain.extractors._message_store import StoredMessage
from m365_brain.extractors._teams_context import TeamsContext
from m365_brain.extractors._teams_ingest import GRAPH_PAGE_SIZE, is_etag_fresh, to_stored_message
from m365_brain.graph_client import GraphClient

log = structlog.get_logger()


def chain_modified(root: dict, replies: list[dict]) -> str:
    """Max lastModifiedDateTime across a root message and all its replies."""
    times = [root.get("lastModifiedDateTime") or root.get("createdDateTime", "")]
    times.extend(r.get("lastModifiedDateTime") or r.get("createdDateTime", "") for r in replies)
    return max(times)


def fetch_chains(
    client: GraphClient,
    team_id: str,
    channel_id: str,
    watermark: str | None,
    max_messages: int,
) -> tuple[list[tuple[dict, list[dict]]], bool]:
    """Fetch (root, replies) chains in chain-modified-descending order.

    Stops paging as soon as a chain at or below the watermark appears (the
    server sort guarantees everything after it is older). During backfill
    (no watermark), stops once ``max_messages`` total messages are collected.
    Returns ``(chains, truncated_by_cap)``.
    """
    url: str | None = f"/teams/{team_id}/channels/{channel_id}/messages"
    params: dict = {"$top": str(GRAPH_PAGE_SIZE), "$expand": "replies"}
    chains: list[tuple[dict, list[dict]]] = []
    total = 0
    truncated = False
    first_page = True

    while url:
        data = client.get(url, params=params if first_page else None)
        first_page = False
        stop = False
        for root in data.get("value", []):
            replies = list(root.get("replies") or [])
            next_link = root.get("replies@odata.nextLink")
            while next_link:
                reply_page = client.get(next_link, params=None)
                replies.extend(reply_page.get("value", []))
                next_link = reply_page.get("@odata.nextLink")

            if watermark and chain_modified(root, replies) <= watermark:
                stop = True
                break
            chains.append((root, replies))
            total += 1 + len(replies)
            if watermark is None and total >= max_messages:
                truncated = True
                stop = True
                break
        if stop:
            break
        url = data.get("@odata.nextLink")

    return chains, truncated


def convert_chains(
    ctx: TeamsContext,
    chains: list[tuple[dict, list[dict]]],
    store: dict[str, StoredMessage],
    base: str,
) -> list[StoredMessage]:
    """Convert fetched chains to StoredMessages, skipping fresh and non-message entries."""
    fetched: list[StoredMessage] = []
    for root, replies in chains:
        root_id = root.get("id", "")
        if root.get("messageType") == "message" and not is_etag_fresh(store.get(root_id), root):
            fetched.append(to_stored_message(ctx, root, None, f"{base}/{root_id}", store.get(root_id)))
        for reply in replies:
            reply_id = reply.get("id", "")
            if reply.get("messageType") != "message" or is_etag_fresh(store.get(reply_id), reply):
                continue
            fetched.append(
                to_stored_message(ctx, reply, root_id, f"{base}/{root_id}/replies/{reply_id}", store.get(reply_id))
            )
    return fetched
