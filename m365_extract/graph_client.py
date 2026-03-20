"""Microsoft Graph API client with pagination, rate limiting, and retry logic.

Accepts a token_provider callable instead of coupling to a specific auth module.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import structlog

from m365_extract.config import GraphConfig

log = structlog.get_logger()

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"


class GraphApiError(Exception):
    """Raised when a Graph API request fails after exhausting retries."""


RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}


class GraphClient:
    """HTTP client for Microsoft Graph API v1.0."""

    def __init__(
        self,
        graph_config: GraphConfig,
        token_provider: Callable[[], str],
    ) -> None:
        self._token_provider = token_provider
        self._max_retries = graph_config.max_retries
        self._backoff_base_seconds = graph_config.backoff_base_ms / 1000.0
        self._max_pages = graph_config.max_pages
        self._client = httpx.Client(
            base_url=GRAPH_BASE_URL,
            timeout=graph_config.timeout_seconds,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> GraphClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token_provider()}",
            "Accept": "application/json",
        }

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict:
        """Execute a GET request against Graph API. Returns the JSON response.

        Handles 401 (token refresh via provider), 429 (rate limit), and transient errors.
        """
        # httpx base_url prepends to all URLs. Absolute URLs from @odata.nextLink
        # and @odata.deltaLink must have the base prefix stripped to avoid double-prepend.
        url = path
        if url.startswith("https://"):
            url = url.removeprefix(GRAPH_BASE_URL)

        for attempt in range(self._max_retries + 1):
            try:
                response = self._client.get(
                    url,
                    headers=self._headers(),
                    params=params,
                )
            except httpx.TransportError as exc:
                if attempt == self._max_retries:
                    raise
                wait = self._backoff_base_seconds * (2**attempt)
                log.warning(
                    "graph.transport_error",
                    error=str(exc),
                    attempt=attempt + 1,
                    wait_seconds=wait,
                )
                time.sleep(wait)
                continue

            if response.status_code == 200:
                return response.json()

            if response.status_code == 401:
                if attempt == 0:
                    log.info("graph.token_expired, refreshing")
                    # Force token refresh by calling provider again on next iteration.
                    # _headers() calls token_provider() each time, so the next
                    # iteration will get a fresh token automatically.
                    continue
                log.error(
                    "graph.401_after_retry",
                    path=path,
                    body=response.text[:500],
                )
                response.raise_for_status()

            if response.status_code in RETRYABLE_STATUS_CODES:
                if attempt == self._max_retries:
                    log.error(
                        "graph.max_retries_exceeded",
                        status=response.status_code,
                        path=path,
                    )
                    response.raise_for_status()

                if response.status_code == 429:
                    retry_after = response.headers.get("Retry-After", "5")
                    wait = float(retry_after)
                else:
                    wait = self._backoff_base_seconds * (2**attempt)

                log.warning(
                    "graph.retryable_error",
                    status=response.status_code,
                    attempt=attempt + 1,
                    wait_seconds=wait,
                )
                time.sleep(wait)
                continue

            log.error(
                "graph.request_failed",
                status=response.status_code,
                path=path,
                body=response.text[:500],
            )
            response.raise_for_status()

        msg = f"Graph API request failed after {self._max_retries} retries: {path}"
        raise GraphApiError(msg)

    def get_bytes(self, url: str) -> bytes:
        """Download binary content from a URL. Returns the raw response bytes.

        Uses the same retry/auth logic as get() but returns response.content
        instead of .json(). Used for downloading files via @microsoft.graph.downloadUrl.
        """
        request_url = url
        if request_url.startswith("https://"):
            request_url = request_url.removeprefix(GRAPH_BASE_URL)

        for attempt in range(self._max_retries + 1):
            try:
                response = self._client.get(
                    request_url,
                    headers=self._headers(),
                )
            except httpx.TransportError as exc:
                if attempt == self._max_retries:
                    raise
                wait = self._backoff_base_seconds * (2**attempt)
                log.warning(
                    "graph.transport_error",
                    error=str(exc),
                    attempt=attempt + 1,
                    wait_seconds=wait,
                )
                time.sleep(wait)
                continue

            if response.status_code == 200:
                return response.content

            if response.status_code == 401:
                if attempt == 0:
                    log.info("graph.token_expired, refreshing")
                    continue
                log.error(
                    "graph.401_after_retry",
                    url=url,
                    body=response.text[:500],
                )
                response.raise_for_status()

            if response.status_code in RETRYABLE_STATUS_CODES:
                if attempt == self._max_retries:
                    log.error(
                        "graph.max_retries_exceeded",
                        status=response.status_code,
                        url=url,
                    )
                    response.raise_for_status()

                if response.status_code == 429:
                    retry_after = response.headers.get("Retry-After", "5")
                    wait = float(retry_after)
                else:
                    wait = self._backoff_base_seconds * (2**attempt)

                log.warning(
                    "graph.retryable_error",
                    status=response.status_code,
                    attempt=attempt + 1,
                    wait_seconds=wait,
                )
                time.sleep(wait)
                continue

            log.error(
                "graph.request_failed",
                status=response.status_code,
                url=url,
                body=response.text[:500],
            )
            response.raise_for_status()

        msg = f"Graph API download failed after {self._max_retries} retries: {url}"
        raise GraphApiError(msg)

    def get_paginated(
        self,
        path: str,
        params: dict[str, Any] | None = None,
        max_pages: int | None = None,
    ) -> Iterator[dict]:
        """Iterate over paginated Graph API results.

        Yields individual items from the 'value' array across all pages.
        Follows @odata.nextLink for pagination.
        Returns the last response's @odata.deltaLink via the final yield if present.
        """
        url = path
        page = 0
        limit = max_pages if max_pages is not None else self._max_pages

        while url and page < limit:
            data = self.get(url, params=params if page == 0 else None)
            items = data.get("value", [])

            yield from items

            url = data.get("@odata.nextLink")
            page += 1

            if url:
                log.debug("graph.following_next_link", page=page)

        if page >= limit:
            log.warning("graph.max_pages_reached", max_pages=limit, path=path)

    def get_delta(
        self,
        path: str,
        delta_link: str | None,
        params: dict[str, Any] | None = None,
        max_pages: int | None = None,
    ) -> tuple[list[dict], str | None]:
        """Execute a delta query. Returns (items, new_delta_link).

        If delta_link is provided, uses it instead of path for incremental sync.
        """
        url = delta_link if delta_link else path
        page = 0
        limit = max_pages if max_pages is not None else self._max_pages
        items: list[dict] = []
        new_delta_link: str | None = None

        while url and page < limit:
            data = self.get(url, params=params if page == 0 and not delta_link else None)
            items.extend(data.get("value", []))

            new_delta_link = data.get("@odata.deltaLink")
            url = data.get("@odata.nextLink")
            page += 1

        if page >= limit:
            log.warning("graph.delta_max_pages_reached", max_pages=limit, path=path)

        return items, new_delta_link
