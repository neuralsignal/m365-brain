"""Microsoft Graph API client with pagination, rate limiting, and retry logic.

Accepts a token_provider callable instead of coupling to a specific auth module.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator
from typing import Any
from urllib.parse import urlparse

import httpx
import structlog

from m365_extract.config import GraphConfig

log = structlog.get_logger()

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"

# Domains trusted for binary downloads (e.g., @microsoft.graph.downloadUrl CDN URLs).
ALLOWED_DOWNLOAD_DOMAINS: frozenset[str] = frozenset(
    {
        ".sharepoint.com",
        ".1drv.com",
        ".microsoft.com",
        ".office.com",
        ".office365.com",
        ".windows.net",
    }
)


class GraphApiError(Exception):
    """Raised when a Graph API request fails after exhausting retries."""


RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

_ERROR_MESSAGE_MAX_LENGTH = 200

# Maps Graph error codes to actionable CLI hints.
_ERROR_HINTS: dict[str, str] = {
    "Authorization_RequestDenied": (
        "The app lacks the required permission. "
        "Go to Entra > App registrations > API permissions and grant the missing scope, then re-consent."
    ),
    "InsufficientPrivileges": (
        "Admin consent is required for this permission. "
        "Ask your tenant admin to grant consent in Entra > App registrations > API permissions."
    ),
    "InvalidAuthenticationToken": (
        "The access token is invalid or expired. Run: m365-extract --config config.yaml auth login"
    ),
    "OrganizationFromTenantGuidNotFound": (
        "The tenant ID in your config does not match a valid Entra tenant. Check MSAL_TENANT_ID in .env."
    ),
    "AuthenticationError": (
        "Authentication failed. Verify MSAL_CLIENT_ID and MSAL_TENANT_ID in .env, "
        "then run: m365-extract --config config.yaml auth login"
    ),
    "ErrorAccessDenied": ("Access denied for this resource. The signed-in user may lack the required role or license."),
}


def _extract_graph_error(body: str) -> tuple[str, str]:
    """Extract error code and message from a Graph API error response.

    Parses the standard ``{"error": {"code": "...", "message": "..."}}``
    envelope. Returns ``("unknown", "non-json response")`` if the body
    is not valid JSON or lacks the expected structure.

    The message is truncated to ``_ERROR_MESSAGE_MAX_LENGTH`` characters
    to prevent PII leakage through verbose error descriptions.
    """
    try:
        data = json.loads(body)
        error = data["error"]
        code = error["code"]
        message = error["message"][:_ERROR_MESSAGE_MAX_LENGTH]
        return code, message
    except (json.JSONDecodeError, KeyError, TypeError):
        return "unknown", "non-json response"


def _is_allowed_download_domain(url: str) -> bool:
    """Check whether a URL's host matches an allowed Microsoft CDN domain."""
    parsed = urlparse(url)
    host = parsed.hostname or ""
    return any(host == suffix.lstrip(".") or host.endswith(suffix) for suffix in ALLOWED_DOWNLOAD_DOMAINS)


def _sanitize_log_url(url: str) -> str:
    """Strip query parameters from a URL to avoid logging SAS tokens."""
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"


def _friendly_error(status: int, error_code: str, error_message: str, path: str) -> str:
    """Build a human-readable error message with an actionable hint if available."""
    hint = _ERROR_HINTS.get(error_code, "")
    parts = [f"Graph API error on {path}: HTTP {status} — {error_code}: {error_message}"]
    if hint:
        parts.append(f"  Hint: {hint}")
    return "\n".join(parts)


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
                error_code, error_message = _extract_graph_error(response.text)
                log.error(
                    "graph.401_after_retry",
                    path=log_ref,
                    error_code=error_code,
                    error_message=error_message,
                )
                raise GraphApiError(_friendly_error(401, error_code, error_message, log_ref))

            if response.status_code in RETRYABLE_STATUS_CODES:
                if attempt == self._max_retries:
                    error_code, error_message = _extract_graph_error(response.text)
                    log.error(
                        "graph.max_retries_exceeded",
                        status=response.status_code,
                        path=log_ref,
                    )
                    raise GraphApiError(_friendly_error(response.status_code, error_code, error_message, log_ref))

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

            error_code, error_message = _extract_graph_error(response.text)
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

    def get(self, path: str, params: dict[str, Any] | None = None) -> dict:
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

        log.info(
            "graph.delta_complete",
            path=path,
            pages_fetched=page,
            items_fetched=len(items),
            delta_link_captured=new_delta_link is not None,
        )

        return items, new_delta_link
