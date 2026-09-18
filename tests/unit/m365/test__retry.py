"""Tests for the Graph retry loop extracted into m365/_retry.py."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from m365_brain.config import GraphConfig
from m365_brain.m365._retry import RequestSpec, RetryPolicy, execute_with_retry, raise_for_response
from m365_brain.m365.errors import (
    AuthTransportError,
    GraphApiError,
    GraphConflictError,
    GraphNotFoundError,
)


@pytest.fixture()
def graph_config() -> GraphConfig:
    return GraphConfig(
        max_retries=2,
        backoff_base_ms=10,
        timeout_seconds=5,
        max_pages=10,
        max_retry_after_seconds=300.0,
        error_message_max_length=200,
    )


def _make_response(status_code: int, text: str = "{}") -> httpx.Response:
    return httpx.Response(status_code=status_code, text=text)


class TestRaiseForResponse:
    def test_404_raises_not_found(self):
        resp = _make_response(404, '{"error":{"code":"ItemNotFound","message":"gone"}}')
        with pytest.raises(GraphNotFoundError) as exc_info:
            raise_for_response(resp, "/me/messages/abc", 200)
        assert exc_info.value.status_code == 404

    def test_412_raises_conflict(self):
        resp = _make_response(412, '{"error":{"code":"Conflict","message":"eTag mismatch"}}')
        with pytest.raises(GraphConflictError) as exc_info:
            raise_for_response(resp, "/me/messages/abc", 200)
        assert exc_info.value.status_code == 412

    def test_other_status_raises_graph_api_error(self):
        resp = _make_response(403, '{"error":{"code":"Forbidden","message":"denied"}}')
        with pytest.raises(GraphApiError) as exc_info:
            raise_for_response(resp, "/path", 200)
        assert exc_info.value.status_code == 403


def _get_request(url: str = "/me") -> RequestSpec:
    return RequestSpec(
        method="GET",
        url=url,
        log_ref="/me",
        params=None,
        body=None,
        content_type=None,
        if_match=None,
    )


class TestExecuteWithRetry:
    def test_success_returns_extracted_value(self, graph_config):
        http = MagicMock()
        http.request.return_value = _make_response(200, '{"ok":true}')
        policy = RetryPolicy(config=graph_config, backoff_base_seconds=0.001)

        result = execute_with_retry(
            http_client=http,
            headers_fn=lambda ct, im: {"Authorization": "Bearer t"},
            policy=policy,
            request=_get_request(),
            extract=lambda r: r.json(),
        )
        assert result == {"ok": True}

    @patch("m365_brain.m365._retry.time.sleep")
    def test_retries_on_transport_error(self, mock_sleep, graph_config):
        http = MagicMock()
        http.request.side_effect = [
            httpx.ConnectError("connection refused"),
            _make_response(200, '{"ok":true}'),
        ]
        policy = RetryPolicy(config=graph_config, backoff_base_seconds=0.001)

        result = execute_with_retry(
            http_client=http,
            headers_fn=lambda ct, im: {},
            policy=policy,
            request=_get_request(),
            extract=lambda r: r.json(),
        )
        assert result == {"ok": True}
        assert http.request.call_count == 2

    @patch("m365_brain.m365._retry.time.sleep")
    def test_retries_on_auth_transport_error(self, mock_sleep, graph_config):
        http = MagicMock()
        http.request.side_effect = [
            AuthTransportError("token fetch failed"),
            _make_response(200, "{}"),
        ]
        policy = RetryPolicy(config=graph_config, backoff_base_seconds=0.001)

        execute_with_retry(
            http_client=http,
            headers_fn=lambda ct, im: {},
            policy=policy,
            request=_get_request(),
            extract=lambda r: r.json(),
        )
        assert http.request.call_count == 2

    @patch("m365_brain.m365._retry.time.sleep")
    def test_exhausted_retries_raises(self, mock_sleep, graph_config):
        http = MagicMock()
        http.request.side_effect = httpx.ConnectError("down")
        policy = RetryPolicy(config=graph_config, backoff_base_seconds=0.001)

        with pytest.raises(httpx.ConnectError):
            execute_with_retry(
                http_client=http,
                headers_fn=lambda ct, im: {},
                policy=policy,
                request=_get_request(),
                extract=lambda r: r.json(),
            )

    def test_non_retryable_status_raises_immediately(self, graph_config):
        http = MagicMock()
        http.request.return_value = _make_response(403, '{"error":{"code":"Forbidden","message":"no"}}')
        policy = RetryPolicy(config=graph_config, backoff_base_seconds=0.001)

        with pytest.raises(GraphApiError) as exc_info:
            execute_with_retry(
                http_client=http,
                headers_fn=lambda ct, im: {},
                policy=policy,
                request=_get_request(),
                extract=lambda r: r.json(),
            )
        assert exc_info.value.status_code == 403
        assert http.request.call_count == 1

    def test_404_raises_not_found(self, graph_config):
        http = MagicMock()
        http.request.return_value = _make_response(404, '{"error":{"code":"ItemNotFound","message":"gone"}}')
        policy = RetryPolicy(config=graph_config, backoff_base_seconds=0.001)

        with pytest.raises(GraphNotFoundError):
            execute_with_retry(
                http_client=http,
                headers_fn=lambda ct, im: {},
                policy=policy,
                request=_get_request(),
                extract=lambda r: r.json(),
            )

    def test_full_url_strips_base(self, graph_config):
        http = MagicMock()
        http.request.return_value = _make_response(200, "{}")
        policy = RetryPolicy(config=graph_config, backoff_base_seconds=0.001)

        execute_with_retry(
            http_client=http,
            headers_fn=lambda ct, im: {},
            policy=policy,
            request=_get_request(url="https://graph.microsoft.com/v1.0/me"),
            extract=lambda r: r.json(),
        )
        call_args = http.request.call_args
        assert call_args[0][1] == "/me"
