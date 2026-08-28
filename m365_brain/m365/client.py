"""The one Microsoft Graph transport: pagination, retry, backoff, write verbs.

Accepts a token_provider callable instead of coupling to a specific auth module.

Two transports used to exist -- a read-only paginating client here and a
write-only retry shell beside the draft sender. They shared a retry loop,
disagreed about its constants, and only one of them had an SSRF guard. This is
the merge: the request shell is method-parametrised, so ``get``, ``post``,
``patch`` and ``put_bytes`` traverse one retry/backoff/401-refresh policy whose
every threshold comes from ``GraphConfig`` rather than a module constant.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any

import httpx
import structlog

from m365_brain.config import GraphConfig

# The Graph three are raised below AND re-exported -- `from m365_brain.m365.client import GraphApiError` is what every
# extractor writes. `AuthTransportError` travels the other way: raised in `m365/auth/`, caught by the retry loop below,
# because `_headers` calls the token provider from inside it. `errors` carries the reasoning for all four.
from m365_brain.m365.errors import AuthTransportError, GraphApiError, GraphConflictError, GraphNotFoundError
from m365_brain.m365.graph_helpers import (
    RETRYABLE_STATUS_CODES,
    _extract_graph_error,
    _friendly_error,
    _retry_wait_seconds,
    validated_download_ref,
)
from m365_brain.m365.pagination import fetch_delta, fetch_pages

log = structlog.get_logger()

GRAPH_BASE_URL = "https://graph.microsoft.com/v1.0"

JSON_CONTENT_TYPE = "application/json"


@dataclass(frozen=True, slots=True)
class _Request:
    """Transport-specific fields for a single Graph API call."""

    method: str
    url: str
    body: str | bytes | None
    content_type: str | None
    if_match: str | None


class GraphClient:
    """HTTP client for Microsoft Graph API v1.0."""

    def __init__(
        self,
        graph_config: GraphConfig,
        token_provider: Callable[[], str],
    ) -> None:
        self._token_provider = token_provider
        self._config = graph_config
        self._backoff_base_seconds = graph_config.backoff_base_ms / 1000.0
        self._client = httpx.Client(
            base_url=GRAPH_BASE_URL,
            timeout=graph_config.timeout_seconds,
            # Graph's /content endpoint 302s to a pre-authenticated CDN URL, and
            # httpx correctly drops Authorization on that cross-origin hop.
            follow_redirects=True,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> GraphClient:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _headers(self, content_type: str | None, if_match: str | None) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self._token_provider()}",
            "Accept": JSON_CONTENT_TYPE,
        }
        if content_type is not None:
            headers["Content-Type"] = content_type
        if if_match is not None:
            headers["If-Match"] = if_match
        return headers

    def _raise_for_response(self, response: httpx.Response, log_ref: str) -> None:
        """Map a non-retryable failure response onto its exception and raise."""
        error_code, error_message = _extract_graph_error(response.text, self._config.error_message_max_length)
        message = _friendly_error(response.status_code, error_code, error_message, log_ref)
        log.error(
            "graph.request_failed",
            status=response.status_code,
            path=log_ref,
            error_code=error_code,
            error_message=error_message,
        )
        if response.status_code == 404:
            raise GraphNotFoundError(message, 404)
        if response.status_code == 412:
            raise GraphConflictError(message, 412)
        raise GraphApiError(message, response.status_code)

    def _execute_with_retry(
        self,
        req: _Request,
        log_ref: str,
        params: dict[str, Any] | None,
        extract: Callable[[httpx.Response], Any],
    ) -> Any:
        """Execute one Graph request with retry, backoff, and token refresh.

        ``extract`` maps a 2xx response to the return value. 404 and 412 raise
        immediately (``GraphNotFoundError`` / ``GraphConflictError``); 401
        refreshes the token once; 429 and 5xx back off.
        """
        request_url = req.url
        if request_url.startswith("https://"):
            request_url = request_url.removeprefix(GRAPH_BASE_URL)

        for attempt in range(self._config.max_retries + 1):
            try:
                response = self._client.request(
                    req.method,
                    request_url,
                    headers=self._headers(req.content_type, req.if_match),
                    params=params,
                    content=req.body,
                )
            except (httpx.TransportError, AuthTransportError) as exc:
                if attempt == self._config.max_retries:
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

            if 200 <= response.status_code < 300:
                return extract(response)

            if response.status_code == 401:
                if attempt == 0:
                    log.info("graph.token_expired, refreshing")
                    continue
                error_code, error_message = _extract_graph_error(response.text, self._config.error_message_max_length)
                log.error(
                    "graph.401_after_retry",
                    path=log_ref,
                    error_code=error_code,
                    error_message=error_message,
                )
                raise GraphApiError(_friendly_error(401, error_code, error_message, log_ref), 401)

            if response.status_code in RETRYABLE_STATUS_CODES and attempt < self._config.max_retries:
                wait = _retry_wait_seconds(
                    response,
                    attempt,
                    self._backoff_base_seconds,
                    self._config.max_retry_after_seconds,
                )
                log.warning(
                    "graph.retryable_error",
                    status=response.status_code,
                    attempt=attempt + 1,
                    wait_seconds=wait,
                )
                time.sleep(wait)
                continue

            if response.status_code in RETRYABLE_STATUS_CODES:
                log.error("graph.max_retries_exceeded", status=response.status_code, path=log_ref)
            self._raise_for_response(response, log_ref)

        msg = f"Graph API request failed after {self._config.max_retries} retries: {log_ref}"
        raise GraphApiError(msg, None)

    @property
    def max_pages(self) -> int:
        """Return the configured maximum number of pages for paginated requests."""
        return self._config.max_pages

    @property
    def config(self) -> GraphConfig:
        """The transport policy this client runs. Collaborators that need a
        timeout or a truncation length read it here instead of being handed
        ``GraphConfig`` a second time alongside the client itself."""
        return self._config

    def _read(
        self,
        url: str,
        log_ref: str,
        params: dict[str, Any] | None,
        extract: Callable[[httpx.Response], Any],
    ) -> Any:
        return self._execute_with_retry(
            _Request(method="GET", url=url, body=None, content_type=None, if_match=None),
            log_ref=log_ref,
            params=params,
            extract=extract,
        )

    def get(self, path: str, params: dict[str, Any] | None) -> dict:
        """Execute a GET request against Graph API. Returns the JSON response."""
        return self._read(path, path, params, lambda r: r.json())

    def post(self, path: str, json_body: dict[str, Any] | None) -> httpx.Response:
        """POST a JSON body, or a bodyless POST when ``json_body`` is None."""
        return self._json_write("POST", path, json_body)

    def patch(self, path: str, json_body: dict[str, Any] | None) -> httpx.Response:
        """PATCH a JSON body through the shared retry shell."""
        return self._json_write("PATCH", path, json_body)

    def _json_write(self, method: str, path: str, json_body: dict[str, Any] | None) -> httpx.Response:
        return self._execute_with_retry(
            _Request(
                method=method,
                url=path,
                body=None if json_body is None else json.dumps(json_body),
                content_type=None if json_body is None else JSON_CONTENT_TYPE,
                if_match=None,
            ),
            log_ref=path,
            params=None,
            extract=lambda r: r,
        )

    def put_bytes(
        self,
        path: str,
        content: bytes,
        content_type: str,
        if_match: str | None,
    ) -> httpx.Response:
        """PUT raw bytes with an explicit Content-Type.

        ``if_match`` carries an eTag for a conditional write, ``None`` for an
        unconditional one. The transport does not decide which is appropriate;
        ``m365/files.py`` owns that policy and never exposes the nullable.
        """
        return self._execute_with_retry(
            _Request(
                method="PUT",
                url=path,
                body=content,
                content_type=content_type,
                if_match=if_match,
            ),
            log_ref=path,
            params=None,
            extract=lambda r: r,
        )

    def get_bytes(self, url: str) -> bytes:
        """Download binary content from a URL (e.g. @microsoft.graph.downloadUrl)."""
        return self._read(url, validated_download_ref(url), None, lambda r: r.content)

    def get_bytes_with_content_type(self, url: str) -> tuple[bytes, str]:
        """Download binary content and return ``(bytes, content_type)``.

        The Teams ``hostedContents/{id}/$value`` endpoint returns inline image
        bytes without revealing the MIME type elsewhere; the response
        ``Content-Type`` header drives the file-extension choice.
        """
        return self._read(
            url,
            validated_download_ref(url),
            None,
            lambda r: (r.content, r.headers.get("Content-Type", "application/octet-stream")),
        )

    def get_pages(self, path: str, params: dict[str, Any] | None, max_pages: int) -> tuple[list[dict], bool]:
        """Fetch up to ``max_pages`` pages of a collection. Returns (items, truncated)."""
        return fetch_pages(self._fetch, path, params, max_pages)

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
        """Execute a delta query. Returns (items, resume_link)."""
        return fetch_delta(self._fetch, path, delta_link, params, max_pages)

    def _fetch(self, url: str, params: dict[str, Any] | None) -> dict:
        """The ``Fetch`` callable the pagination loops drive."""
        return self.get(url, params=params)
