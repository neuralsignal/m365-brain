"""Microsoft Graph API client with pagination, rate limiting, and retry logic.

Accepts a token_provider callable instead of coupling to a specific auth module.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import structlog

from m365_brain.config import GraphConfig
from m365_brain.graph_helpers import (
    RETRYABLE_STATUS_CODES,
    _extract_graph_error,
    _friendly_error,
    _is_allowed_download_domain,
    _sanitize_log_url,
)

log = structlog.get_logger()

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"


class GraphApiError(Exception):
    """Raised when a Graph API request fails after exhausting retries.

    ``status_code`` carries the HTTP status when the failure came from an HTTP
    response (``None`` for logical/transport-level failures).
    """

    def __init__(self, message: str, status_code: int | None) -> None:
        super().__init__(message)
        self.status_code = status_code


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

        Handles 401 (token refresh), 429, and transient errors; ``extract``
        maps a successful response to the return value.
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
                raise GraphApiError(_friendly_error(401, error_code, error_message, log_ref), 401)

            if response.status_code in RETRYABLE_STATUS_CODES:
                if attempt == self._max_retries:
                    error_code, error_message = _extract_graph_error(response.text, self._error_message_max_length)
                    log.error(
                        "graph.max_retries_exceeded",
                        status=response.status_code,
                        path=log_ref,
                    )
                    raise GraphApiError(
                        _friendly_error(response.status_code, error_code, error_message, log_ref),
                        response.status_code,
                    )

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
            raise GraphApiError(
                _friendly_error(response.status_code, error_code, error_message, log_ref),
                response.status_code,
            )

        msg = f"Graph API request failed after {self._max_retries} retries: {log_ref}"
        raise GraphApiError(msg, None)

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

    def _validated_download_ref(self, url: str) -> str:
        """SSRF-guard a download URL; return a SAS-token-free log reference.

        Absolute URLs must point at allowed Microsoft CDN domains; query
        parameters are stripped from the log reference (SAS tokens).
        """
        if not url.startswith("https://"):
            return url
        if not _is_allowed_download_domain(url):
            raise GraphApiError(
                f"Download URL blocked: host is not an allowed Microsoft domain: {_sanitize_log_url(url)}",
                None,
            )
        return _sanitize_log_url(url)

    def get_bytes(self, url: str) -> bytes:
        """Download binary content from a URL (e.g. @microsoft.graph.downloadUrl)."""
        return self._execute_with_retry(
            url=url,
            log_ref=self._validated_download_ref(url),
            params=None,
            extract=lambda r: r.content,
        )

    def get_bytes_with_content_type(self, url: str) -> tuple[bytes, str]:
        """Download binary content and return ``(bytes, content_type)``.

        The Teams ``hostedContents/{id}/$value`` endpoint returns inline image
        bytes without revealing the MIME type elsewhere; the response
        ``Content-Type`` header drives the file-extension choice.
        """
        return self._execute_with_retry(
            url=url,
            log_ref=self._validated_download_ref(url),
            params=None,
            extract=lambda r: (r.content, r.headers.get("Content-Type", "application/octet-stream")),
        )

    def get_pages(self, path: str, params: dict[str, Any] | None, max_pages: int) -> tuple[list[dict], bool]:
        """Fetch up to ``max_pages`` pages of a collection. Returns (items, truncated).

        ``truncated`` is True when an @odata.nextLink remained unfetched at the
        page cap — the only reliable completeness signal for capped fetches.
        """
        url: str | None = path
        page = 0
        items: list[dict] = []

        while url and page < max_pages:
            data = self.get(url, params=params if page == 0 else None)
            items.extend(data.get("value", []))
            url = data.get("@odata.nextLink")
            page += 1

        truncated = bool(url)
        if url:
            log.warning("graph.max_pages_reached", max_pages=max_pages, path=path, next_link=_sanitize_log_url(url))
        return items, truncated

    def get_paginated(self, path: str, params: dict[str, Any] | None, max_pages: int) -> Iterator[dict]:
        """Iterate items from a paginated collection (thin wrapper over ``get_pages``)."""
        items, _ = self.get_pages(path, params, max_pages)
        yield from items

    def get_delta(
        self,
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
            data = self.get(url, params=params if page == 0 and not delta_link else None)
            items.extend(data.get("value", []))

            new_delta_link = data.get("@odata.deltaLink")
            url = data.get("@odata.nextLink")
            page += 1

        if url:
            ref = _sanitize_log_url(url)
            log.warning("graph.delta_max_pages_reached", max_pages=max_pages, path=path, next_link=ref)
            new_delta_link = url

        log.info(
            "graph.delta_complete",
            path=path,
            pages_fetched=page,
            items_fetched=len(items),
            delta_link_captured=new_delta_link is not None,
        )

        return items, new_delta_link
