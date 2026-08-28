"""Retry, backoff and error mapping for a single Graph request.

Extracted from ``client.py`` so that module stays focused on the
``GraphClient`` class surface. The retry loop is the transport's
heaviest policy and the part most likely to grow (circuit-breaker,
jitter, per-verb budgets), so it gets its own module.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import httpx
import structlog

from m365_brain.config import GraphConfig
from m365_brain.m365.errors import AuthTransportError, GraphApiError, GraphConflictError, GraphNotFoundError
from m365_brain.m365.graph_helpers import (
    GRAPH_BASE_URL,
    RETRYABLE_STATUS_CODES,
    _extract_graph_error,
    _friendly_error,
    _retry_wait_seconds,
)

log = structlog.get_logger()


def raise_for_response(response: httpx.Response, log_ref: str, error_message_max_length: int) -> None:
    """Map a non-retryable failure response onto its exception and raise."""
    error_code, error_message = _extract_graph_error(response.text, error_message_max_length)
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


def execute_with_retry(
    http_client: httpx.Client,
    headers_fn: Callable[[str | None, str | None], dict[str, str]],
    config: GraphConfig,
    backoff_base_seconds: float,
    url: str,
    log_ref: str,
    params: dict[str, Any] | None,
    extract: Callable[[httpx.Response], Any],
    method: str,
    body: str | bytes | None,
    content_type: str | None,
    if_match: str | None,
) -> Any:
    """Execute one Graph request with retry, backoff, and token refresh.

    ``extract`` maps a 2xx response to the return value. 404 and 412 raise
    immediately (``GraphNotFoundError`` / ``GraphConflictError``); 401
    refreshes the token once; 429 and 5xx back off.
    """
    request_url = url
    if request_url.startswith("https://"):
        request_url = request_url.removeprefix(GRAPH_BASE_URL)

    for attempt in range(config.max_retries + 1):
        try:
            response = http_client.request(
                method,
                request_url,
                headers=headers_fn(content_type, if_match),
                params=params,
                content=body,
            )
        except (httpx.TransportError, AuthTransportError) as exc:
            if attempt == config.max_retries:
                raise
            wait = backoff_base_seconds * (2**attempt)
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
            error_code, error_message = _extract_graph_error(response.text, config.error_message_max_length)
            log.error(
                "graph.401_after_retry",
                path=log_ref,
                error_code=error_code,
                error_message=error_message,
            )
            raise GraphApiError(_friendly_error(401, error_code, error_message, log_ref), 401)

        if response.status_code in RETRYABLE_STATUS_CODES and attempt < config.max_retries:
            wait = _retry_wait_seconds(
                response,
                attempt,
                backoff_base_seconds,
                config.max_retry_after_seconds,
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
        raise_for_response(response, log_ref, config.error_message_max_length)

    msg = f"Graph API request failed after {config.max_retries} retries: {log_ref}"
    raise GraphApiError(msg, None)
