"""The one Microsoft Graph transport: pagination, retry, backoff, write verbs.

Accepts a token_provider callable instead of coupling to a specific auth module.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import structlog

from m365_brain.config import GraphConfig
from m365_brain.m365._retry import RequestSpec, RetryPolicy, execute_with_retry

# Re-exported — `from m365_brain.m365.client import GraphApiError` is what every extractor writes.
from m365_brain.m365.errors import AuthTransportError as AuthTransportError  # noqa: F401
from m365_brain.m365.errors import GraphApiError as GraphApiError  # noqa: F401
from m365_brain.m365.errors import GraphConflictError as GraphConflictError  # noqa: F401
from m365_brain.m365.errors import GraphNotFoundError as GraphNotFoundError  # noqa: F401
from m365_brain.m365.graph_helpers import GRAPH_BASE_URL, validated_download_ref
from m365_brain.m365.pagination import fetch_delta, fetch_pages

log = structlog.get_logger()

JSON_CONTENT_TYPE = "application/json"
IMMUTABLE_ID_PREFERENCE = 'IdType="ImmutableId"'


class GraphClient:
    """HTTP client for Microsoft Graph API v1.0."""

    def __init__(
        self,
        graph_config: GraphConfig,
        token_provider: Callable[[], str],
        *,
        prefer_immutable_ids: bool,
    ) -> None:
        """`prefer_immutable_ids` asks Graph for Outlook ids that survive a folder move.

        A default id changes when a message moves, and sending a draft moves it
        from Drafts to Sent Items. An outbox that stored the default id reads
        every sent draft as a 404, i.e. as deleted. The sync keeps default ids
        because the vault is keyed on them.
        """
        self._token_provider = token_provider
        self._config = graph_config
        self._prefer_immutable_ids = prefer_immutable_ids
        self._retry_policy = RetryPolicy(
            config=graph_config,
            backoff_base_seconds=graph_config.backoff_base_ms / 1000.0,
        )
        self._client = httpx.Client(
            base_url=GRAPH_BASE_URL,
            timeout=graph_config.timeout_seconds,
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
        if self._prefer_immutable_ids:
            headers["Prefer"] = IMMUTABLE_ID_PREFERENCE
        return headers

    def _execute_with_retry(
        self,
        request: RequestSpec,
        extract: Callable[[httpx.Response], Any],
    ) -> Any:
        return execute_with_retry(
            http_client=self._client,
            headers_fn=self._headers,
            policy=self._retry_policy,
            request=request,
            extract=extract,
        )

    @property
    def max_pages(self) -> int:
        """Return the configured maximum number of pages for paginated requests."""
        return self._config.max_pages

    @property
    def config(self) -> GraphConfig:
        """The transport policy this client runs."""
        return self._config

    def _read(
        self,
        url: str,
        log_ref: str,
        params: dict[str, Any] | None,
        extract: Callable[[httpx.Response], Any],
    ) -> Any:
        return self._execute_with_retry(
            RequestSpec(
                method="GET",
                url=url,
                log_ref=log_ref,
                params=params,
                body=None,
                content_type=None,
                if_match=None,
            ),
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
            RequestSpec(
                method=method,
                url=path,
                log_ref=path,
                params=None,
                body=None if json_body is None else json.dumps(json_body),
                content_type=None if json_body is None else JSON_CONTENT_TYPE,
                if_match=None,
            ),
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
            RequestSpec(
                method="PUT",
                url=path,
                log_ref=path,
                params=None,
                body=content,
                content_type=content_type,
                if_match=if_match,
            ),
            extract=lambda r: r,
        )

    def get_bytes(self, url: str) -> bytes:
        """Download binary content from a URL (e.g. @microsoft.graph.downloadUrl)."""
        return self._read(url, validated_download_ref(url), None, lambda r: r.content)

    def get_bytes_with_content_type(self, url: str) -> tuple[bytes, str]:
        """Download binary content and return ``(bytes, content_type)``."""
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
