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
from m365_extract.graph_helpers import (
    RETRYABLE_STATUS_CODES,
    _extract_graph_error,
    _friendly_error,
    _is_allowed_download_domain,
    _sanitize_log_url,
)

log = structlog.get_logger()

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"


class GraphApiError(Exception):
    """Raised when a Graph API request fails after exhausting retries."""


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
        self._max_retry_after_seconds = graph_config.max_retry_after_seconds
        self._error_message_max_length = graph_config.error_message_max_length
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

    def _execute_with_retry(
        self,
        url: str,
        log_ref: str,
        params: dict[str, Any] | None,
        extract: Callable[[httpx.Response], Any],
    ) -> Any:
        """Execute a GET request with retry, backoff, and token-refresh logic.

        Handles 401 (token refresh via provider), 429 (rate limit), and transient errors.
        The extract callable determines what to return from a successful response.
        """
        request_url = url
        if request_url.startswith("https://"):
            request_url = request_url.removeprefix(GRAPH_BASE_URL)

        for attempt in range(self._max_retries + 1):
            try:
                response = self._client.get(
                    request_url,
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
                return extract(response)

            if response.status_code == 401:
                if attempt == 0:
                    log.info("graph.token_expired, refreshing")
                    continue
                error_code, error_message = _extract_graph_error(response.text, self._error_message_max_length)
                log.error(
                    "graph.401_after_retry",
                    path=log_ref,
                    error_code=error_code,
                    error_message=error_message,
                )
                raise GraphApiError(_friendly_error(401, error_code, error_message, log_ref))

            if response.status_code in RETRYABLE_STATUS_CODES:
                if attempt == self._max_retries:
                    error_code, error_message = _extract_graph_error(response.text, self._error_message_max_length)
                    log.error(
                        "graph.max_retries_exceeded",
                        status=response.status_code,
                        path=log_ref,
                    )
                    raise GraphApiError(_friendly_error(response.status_code, error_code, error_message, log_ref))

                if response.status_code == 429:
                    retry_after = response.headers.get("Retry-After", "5")
                    try:
                        wait = min(float(retry_after), self._max_retry_after_seconds)
                    except (ValueError, TypeError):
                        wait = self._backoff_base_seconds * (2**attempt)
                        log.warning(
                            "graph.invalid_retry_after",
                            retry_after=retry_after,
                            fallback_wait=wait,
                        )
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

            error_code, error_message = _extract_graph_error(response.text, self._error_message_max_length)
            log.error(
                "graph.request_failed",
                status=response.status_code,
                path=log_ref,
                error_code=error_code,
                error_message=error_message,
            )
            raise GraphApiError(_friendly_error(response.status_code, error_code, error_message, log_ref))

        msg = f"Graph API request failed after {self._max_retries} retries: {log_ref}"
        raise GraphApiError(msg)

    @property
    def max_pages(self) -> int:
        """Return the configured maximum number of pages for paginated requests."""
        return self._max_pages

    def get(self, path: str, params: dict[str, Any] | None) -> dict:
        """Execute a GET request against Graph API. Returns the JSON response."""
        return self._execute_with_retry(
            url=path,
            log_ref=path,
            params=params,
            extract=lambda r: r.json(),
        )

    def get_bytes(self, url: str) -> bytes:
        """Download binary content from a URL. Returns the raw response bytes.

        Used for downloading files via @microsoft.graph.downloadUrl.
        Validates that absolute URLs point to allowed Microsoft CDN domains
        to prevent SSRF. Strips query parameters from log output to avoid
        leaking SAS tokens.
        """
        if url.startswith("https://") and not _is_allowed_download_domain(url):
            raise GraphApiError(
                f"Download URL blocked: host is not an allowed Microsoft domain: {_sanitize_log_url(url)}"
            )

        return self._execute_with_retry(
            url=url,
            log_ref=_sanitize_log_url(url) if url.startswith("https://") else url,
            params=None,
            extract=lambda r: r.content,
        )

    def get_bytes_with_content_type(self, url: str) -> tuple[bytes, str]:
        """Download binary content and return ``(bytes, content_type)``.

        The Teams ``hostedContents/{id}/$value`` endpoint returns inline image
        bytes without revealing the MIME type elsewhere; we need the response
        ``Content-Type`` header to choose a file extension. ``url`` may be a
        relative Graph path or an absolute Microsoft CDN URL (validated by
        the same SSRF guard as ``get_bytes``).
        """
        if url.startswith("https://") and not _is_allowed_download_domain(url):
            raise GraphApiError(
                f"Download URL blocked: host is not an allowed Microsoft domain: {_sanitize_log_url(url)}"
            )

        return self._execute_with_retry(
            url=url,
            log_ref=_sanitize_log_url(url) if url.startswith("https://") else url,
            params=None,
            extract=lambda r: (r.content, r.headers.get("Content-Type", "application/octet-stream")),
        )

    def get_paginated(
        self,
        path: str,
        params: dict[str, Any] | None,
        max_pages: int,
    ) -> Iterator[dict]:
        """Iterate over paginated Graph API results.

        Yields individual items from the 'value' array across all pages.
        Follows @odata.nextLink for pagination.
        Returns the last response's @odata.deltaLink via the final yield if present.
        """
        url = path
        page = 0
        limit = max_pages

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
        params: dict[str, Any] | None,
        max_pages: int,
    ) -> tuple[list[dict], str | None]:
        """Execute a delta query. Returns (items, new_delta_link).

        If delta_link is provided, uses it instead of path for incremental sync.
        """
        url = delta_link if delta_link else path
        page = 0
        limit = max_pages
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

        log.info(
            "graph.delta_complete",
            path=path,
            pages_fetched=page,
            items_fetched=len(items),
            delta_link_captured=new_delta_link is not None,
        )

        return items, new_delta_link
