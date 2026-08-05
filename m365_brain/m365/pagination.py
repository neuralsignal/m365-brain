"""Graph collection paging: page caps, ``@odata.nextLink``, delta resume links.

Pure loop logic over a ``Fetch`` callable, so it is exercisable without HTTP
and keeps ``client.py`` to the verb surface plus the retry shell. ``GraphClient``
delegates its three paging methods here; nothing else calls these directly.

The page cap is not a nicety. Graph will happily hand back an unbounded chain of
pages, and a cycle that walks all of them is a cycle that never ends -- so both
functions stop at ``max_pages`` and report what that cost: ``get_pages``
returns a ``truncated`` flag, ``fetch_delta`` returns the pending
``@odata.nextLink`` as the resume link so the next round continues rather than
restarting.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import structlog

from m365_brain.m365.graph_helpers import _sanitize_log_url

log = structlog.get_logger()

type Fetch = Callable[[str, dict[str, Any] | None], dict]
"""``(url, params) -> json``. ``GraphClient.get`` satisfies it."""


def fetch_pages(fetch: Fetch, path: str, params: dict[str, Any] | None, max_pages: int) -> tuple[list[dict], bool]:
    """Fetch up to ``max_pages`` pages of a collection. Returns (items, truncated).

    ``truncated`` is True when an @odata.nextLink remained unfetched at the
    page cap — the only reliable completeness signal for capped fetches.
    """
    url: str | None = path
    page = 0
    items: list[dict] = []

    while url and page < max_pages:
        data = fetch(url, params if page == 0 else None)
        items.extend(data.get("value", []))
        url = data.get("@odata.nextLink")
        page += 1

    truncated = bool(url)
    if url:
        log.warning("graph.max_pages_reached", max_pages=max_pages, path=path, next_link=_sanitize_log_url(url))
    return items, truncated


def fetch_delta(
    fetch: Fetch,
    path: str,
    delta_link: str | None,
    params: dict[str, Any] | None,
    max_pages: int,
) -> tuple[list[dict], str | None]:
    """Execute a delta query. Returns (items, resume_link).

    Uses delta_link instead of path when provided. When the page cap
    interrupts the round, the pending @odata.nextLink is the resume link.
    """
    url = delta_link if delta_link else path
    page = 0
    items: list[dict] = []
    new_delta_link: str | None = None

    while url and page < max_pages:
        data = fetch(url, params if page == 0 and not delta_link else None)
        items.extend(data.get("value", []))

        new_delta_link = data.get("@odata.deltaLink")
        url = data.get("@odata.nextLink")
        page += 1

    if url:
        log.warning("graph.delta_max_pages_reached", max_pages=max_pages, path=path, next_link=_sanitize_log_url(url))
        new_delta_link = url

    log.info(
        "graph.delta_complete",
        path=path,
        pages_fetched=page,
        items_fetched=len(items),
        delta_link_captured=new_delta_link is not None,
    )

    return items, new_delta_link
