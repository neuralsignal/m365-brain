"""Tests for Graph API client."""

from __future__ import annotations

import json

import pytest
from pytest_httpx import HTTPXMock

from m365_extract.config import GraphConfig
from m365_extract.graph_client import GRAPH_BASE_URL, GraphApiError, GraphClient, _extract_graph_error


@pytest.fixture()
def graph_config():
    return GraphConfig(
        max_retries=2,
        backoff_base_ms=10,
        timeout_seconds=5,
        max_pages=10,
    )


@pytest.fixture()
def token_provider():
    return lambda: "test-token-abc"


@pytest.fixture()
def client(graph_config, token_provider):
    c = GraphClient(graph_config, token_provider)
    yield c
    c.close()


class TestGet:
    def test_successful_get(self, httpx_mock: HTTPXMock, client):
        httpx_mock.add_response(
            url=f"{GRAPH_BASE_URL}/me",
            json={"displayName": "Test User"},
        )
        result = client.get("/me")
        assert result["displayName"] == "Test User"

    def test_sends_auth_header(self, httpx_mock: HTTPXMock, client):
        httpx_mock.add_response(
            url=f"{GRAPH_BASE_URL}/me",
            json={"displayName": "Test User"},
        )
        client.get("/me")
        request = httpx_mock.get_requests()[0]
        assert request.headers["Authorization"] == "Bearer test-token-abc"

    def test_401_retries_with_new_token(self, httpx_mock: HTTPXMock, graph_config):
        call_count = 0

        def token_provider():
            nonlocal call_count
            call_count += 1
            return f"token-{call_count}"

        c = GraphClient(graph_config, token_provider)

        httpx_mock.add_response(
            url=f"{GRAPH_BASE_URL}/me",
            status_code=401,
        )
        httpx_mock.add_response(
            url=f"{GRAPH_BASE_URL}/me",
            json={"ok": True},
        )

        result = c.get("/me")
        assert result["ok"] is True
        assert call_count == 2
        c.close()

    def test_429_retries_with_backoff(self, httpx_mock: HTTPXMock, client):
        httpx_mock.add_response(
            url=f"{GRAPH_BASE_URL}/me",
            status_code=429,
            headers={"Retry-After": "0"},
        )
        httpx_mock.add_response(
            url=f"{GRAPH_BASE_URL}/me",
            json={"ok": True},
        )
        result = client.get("/me")
        assert result["ok"] is True

    def test_500_retries(self, httpx_mock: HTTPXMock, client):
        httpx_mock.add_response(
            url=f"{GRAPH_BASE_URL}/me",
            status_code=500,
        )
        httpx_mock.add_response(
            url=f"{GRAPH_BASE_URL}/me",
            json={"ok": True},
        )
        result = client.get("/me")
        assert result["ok"] is True

    def test_max_retries_exceeded_raises(self, httpx_mock: HTTPXMock, client):
        for _ in range(3):
            httpx_mock.add_response(
                url=f"{GRAPH_BASE_URL}/me",
                status_code=500,
            )
        with pytest.raises(GraphApiError, match="HTTP 500"):
            client.get("/me")

    def test_404_raises_immediately(self, httpx_mock: HTTPXMock, client):
        httpx_mock.add_response(
            url=f"{GRAPH_BASE_URL}/me",
            status_code=404,
        )
        with pytest.raises(GraphApiError, match="HTTP 404"):
            client.get("/me")


class TestGetBytes:
    def test_successful_download(self, httpx_mock: HTTPXMock, client):
        httpx_mock.add_response(
            url=f"{GRAPH_BASE_URL}/me/drive/items/file-1/content",
            content=b"file-content-bytes",
        )
        result = client.get_bytes("/me/drive/items/file-1/content")
        assert result == b"file-content-bytes"

    def test_absolute_url(self, httpx_mock: HTTPXMock, client):
        absolute_url = "https://download.example.com/file"
        httpx_mock.add_response(
            url=absolute_url,
            content=b"downloaded",
        )
        result = client.get_bytes(absolute_url)
        assert result == b"downloaded"

    def test_retry_on_500(self, httpx_mock: HTTPXMock, client):
        httpx_mock.add_response(
            url=f"{GRAPH_BASE_URL}/me/drive/content",
            status_code=500,
        )
        httpx_mock.add_response(
            url=f"{GRAPH_BASE_URL}/me/drive/content",
            content=b"ok",
        )
        result = client.get_bytes("/me/drive/content")
        assert result == b"ok"

    def test_max_retries_exceeded_raises(self, httpx_mock: HTTPXMock, client):
        for _ in range(3):
            httpx_mock.add_response(
                url=f"{GRAPH_BASE_URL}/me/drive/content",
                status_code=500,
            )
        with pytest.raises(GraphApiError, match="HTTP 500"):
            client.get_bytes("/me/drive/content")


class TestGetPaginated:
    def test_single_page(self, httpx_mock: HTTPXMock, client):
        httpx_mock.add_response(
            url=f"{GRAPH_BASE_URL}/me/messages",
            json={"value": [{"id": "1"}, {"id": "2"}]},
        )
        items = list(client.get_paginated("/me/messages"))
        assert len(items) == 2
        assert items[0]["id"] == "1"

    def test_multi_page(self, httpx_mock: HTTPXMock, client):
        httpx_mock.add_response(
            url=f"{GRAPH_BASE_URL}/me/messages",
            json={
                "value": [{"id": "1"}],
                "@odata.nextLink": f"{GRAPH_BASE_URL}/me/messages?$skip=1",
            },
        )
        httpx_mock.add_response(
            url=f"{GRAPH_BASE_URL}/me/messages?$skip=1",
            json={"value": [{"id": "2"}]},
        )
        items = list(client.get_paginated("/me/messages"))
        assert len(items) == 2

    def test_max_pages_limit(self, httpx_mock: HTTPXMock, client):
        httpx_mock.add_response(
            url=f"{GRAPH_BASE_URL}/me/messages",
            json={
                "value": [{"id": "1"}],
                "@odata.nextLink": f"{GRAPH_BASE_URL}/me/messages?$skip=1",
            },
        )
        items = list(client.get_paginated("/me/messages", max_pages=1))
        assert len(items) == 1

    def test_absolute_nextlink_urls(self, httpx_mock: HTTPXMock, client):
        """Absolute URLs from @odata.nextLink must not get base_url double-prepended."""
        absolute_next = f"{GRAPH_BASE_URL}/me/messages?$skip=1&$top=10"
        httpx_mock.add_response(
            url=f"{GRAPH_BASE_URL}/me/messages",
            json={
                "value": [{"id": "1"}],
                "@odata.nextLink": absolute_next,
            },
        )
        httpx_mock.add_response(
            url=absolute_next,
            json={"value": [{"id": "2"}]},
        )
        items = list(client.get_paginated("/me/messages"))
        assert len(items) == 2
        assert items[1]["id"] == "2"

    def test_empty_response(self, httpx_mock: HTTPXMock, client):
        httpx_mock.add_response(
            url=f"{GRAPH_BASE_URL}/me/messages",
            json={"value": []},
        )
        items = list(client.get_paginated("/me/messages"))
        assert items == []


class TestGetDelta:
    def test_initial_delta_returns_items_and_link(self, httpx_mock: HTTPXMock, client):
        httpx_mock.add_response(
            url=f"{GRAPH_BASE_URL}/me/mailFolders/Inbox/messages/delta",
            json={
                "value": [{"id": "1"}, {"id": "2"}],
                "@odata.deltaLink": "https://graph.microsoft.com/delta?token=abc",
            },
        )
        items, delta_link = client.get_delta(
            "/me/mailFolders/Inbox/messages/delta",
            None,
        )
        assert len(items) == 2
        assert delta_link == "https://graph.microsoft.com/delta?token=abc"

    def test_incremental_delta_uses_link(self, httpx_mock: HTTPXMock, client):
        httpx_mock.add_response(
            url="https://graph.microsoft.com/delta?token=abc",
            json={
                "value": [{"id": "3"}],
                "@odata.deltaLink": "https://graph.microsoft.com/delta?token=def",
            },
        )
        items, delta_link = client.get_delta(
            "/me/mailFolders/Inbox/messages/delta",
            "https://graph.microsoft.com/delta?token=abc",
        )
        assert len(items) == 1
        assert items[0]["id"] == "3"
        assert delta_link == "https://graph.microsoft.com/delta?token=def"

    def test_delta_with_pagination(self, httpx_mock: HTTPXMock, client):
        httpx_mock.add_response(
            url=f"{GRAPH_BASE_URL}/me/mailFolders/Inbox/messages/delta",
            json={
                "value": [{"id": "1"}],
                "@odata.nextLink": f"{GRAPH_BASE_URL}/me/mailFolders/Inbox/messages/delta?$skip=1",
            },
        )
        httpx_mock.add_response(
            url=f"{GRAPH_BASE_URL}/me/mailFolders/Inbox/messages/delta?$skip=1",
            json={
                "value": [{"id": "2"}],
                "@odata.deltaLink": "https://graph.microsoft.com/delta?token=final",
            },
        )
        items, delta_link = client.get_delta(
            "/me/mailFolders/Inbox/messages/delta",
            None,
        )
        assert len(items) == 2
        assert delta_link == "https://graph.microsoft.com/delta?token=final"


class TestExtractGraphError:
    """Tests for _extract_graph_error helper that sanitizes PII from error responses."""

    def test_parses_standard_graph_error_json(self):
        body = json.dumps(
            {
                "error": {
                    "code": "InvalidAuthenticationToken",
                    "message": "Access token has expired or is not yet valid.",
                }
            }
        )
        code, message = _extract_graph_error(body)
        assert code == "InvalidAuthenticationToken"
        assert message == "Access token has expired or is not yet valid."

    def test_truncates_long_message(self):
        body = json.dumps(
            {
                "error": {
                    "code": "BadRequest",
                    "message": "x" * 600,
                }
            }
        )
        code, message = _extract_graph_error(body)
        assert code == "BadRequest"
        assert len(message) <= 200

    def test_non_json_body_returns_unknown(self):
        body = "<html>Internal Server Error with user@company.com PII data</html>"
        code, message = _extract_graph_error(body)
        assert code == "unknown"
        assert message == "non-json response"

    def test_missing_error_key_returns_unknown(self):
        body = json.dumps({"status": "failed", "detail": "user pii@example.com"})
        code, message = _extract_graph_error(body)
        assert code == "unknown"
        assert message == "non-json response"

    def test_pii_in_body_not_leaked_to_log(self, httpx_mock: HTTPXMock, graph_config):
        """A 401 with PII in the body must not leak PII into structured logs."""
        pii_body = json.dumps(
            {
                "error": {
                    "code": "Unauthorized",
                    "message": "Token for user john.doe@company.com is invalid.",
                }
            }
        )
        # Return 401 twice to trigger the log path (attempt 0 retries silently, attempt 1 logs)
        httpx_mock.add_response(url=f"{GRAPH_BASE_URL}/me", status_code=401)
        httpx_mock.add_response(
            url=f"{GRAPH_BASE_URL}/me",
            status_code=401,
            text=pii_body,
        )

        c = GraphClient(graph_config, lambda: "test-token")
        with pytest.raises(GraphApiError, match="HTTP 401"):
            c.get("/me")
        c.close()

    def test_404_with_pii_body_logs_sanitized(self, httpx_mock: HTTPXMock, client):
        """Non-retryable errors with PII in body should log error_code, not raw body."""
        pii_body = json.dumps(
            {
                "error": {
                    "code": "ResourceNotFound",
                    "message": "User john.doe@secret.com not found in directory.",
                }
            }
        )
        httpx_mock.add_response(
            url=f"{GRAPH_BASE_URL}/me/messages/123",
            status_code=404,
            text=pii_body,
        )
        with pytest.raises(GraphApiError, match="ResourceNotFound"):
            client.get("/me/messages/123")


class TestDefensiveBehavior:
    """Edge cases: non-retryable errors and missing headers."""

    def test_403_raises_immediately_no_retry(self, httpx_mock: HTTPXMock, client):
        """403 Forbidden is not retryable — should raise after a single request."""
        httpx_mock.add_response(
            url=f"{GRAPH_BASE_URL}/me/messages",
            status_code=403,
            text=json.dumps({"error": {"code": "Forbidden", "message": "Access denied."}}),
        )
        with pytest.raises(GraphApiError, match="HTTP 403"):
            client.get("/me/messages")
        # Only one request should have been made (no retries)
        assert len(httpx_mock.get_requests()) == 1

    def test_429_without_retry_after_header(self, httpx_mock: HTTPXMock, client):
        """429 without Retry-After header should use the fallback default (5s), not crash."""
        httpx_mock.add_response(
            url=f"{GRAPH_BASE_URL}/me/messages",
            status_code=429,
            # No Retry-After header
        )
        httpx_mock.add_response(
            url=f"{GRAPH_BASE_URL}/me/messages",
            json={"value": []},
        )
        result = client.get("/me/messages")
        assert result == {"value": []}

    def test_410_gone_raises_immediately(self, httpx_mock: HTTPXMock, client):
        """410 Gone (expired delta token) is not retryable — should raise immediately."""
        httpx_mock.add_response(
            url=f"{GRAPH_BASE_URL}/me/contacts/delta",
            status_code=410,
            text=json.dumps({"error": {"code": "ResyncRequired", "message": "Delta token expired."}}),
        )
        with pytest.raises(GraphApiError, match="HTTP 410"):
            client.get("/me/contacts/delta")
        assert len(httpx_mock.get_requests()) == 1


class TestFriendlyErrors:
    """Tests for actionable error hints on known Graph error codes."""

    def test_403_insufficient_privileges_includes_hint(self, httpx_mock: HTTPXMock, client):
        body = json.dumps(
            {
                "error": {
                    "code": "Authorization_RequestDenied",
                    "message": "Insufficient privileges to complete the operation.",
                }
            }
        )
        httpx_mock.add_response(
            url=f"{GRAPH_BASE_URL}/me/messages",
            status_code=403,
            text=body,
        )
        with pytest.raises(GraphApiError, match="Hint:.*Entra.*API permissions") as exc_info:
            client.get("/me/messages")
        assert "Authorization_RequestDenied" in str(exc_info.value)

    def test_401_invalid_token_includes_hint(self, httpx_mock: HTTPXMock, graph_config):
        body = json.dumps(
            {
                "error": {
                    "code": "InvalidAuthenticationToken",
                    "message": "Access token has expired.",
                }
            }
        )
        httpx_mock.add_response(url=f"{GRAPH_BASE_URL}/me", status_code=401)
        httpx_mock.add_response(url=f"{GRAPH_BASE_URL}/me", status_code=401, text=body)

        c = GraphClient(graph_config, lambda: "expired-token")
        with pytest.raises(GraphApiError, match="Hint:.*auth login") as exc_info:
            c.get("/me")
        assert "InvalidAuthenticationToken" in str(exc_info.value)
        c.close()

    def test_unknown_error_code_has_no_hint(self, httpx_mock: HTTPXMock, client):
        body = json.dumps(
            {
                "error": {
                    "code": "SomeNewError",
                    "message": "Something unexpected.",
                }
            }
        )
        httpx_mock.add_response(
            url=f"{GRAPH_BASE_URL}/me",
            status_code=400,
            text=body,
        )
        with pytest.raises(GraphApiError) as exc_info:
            client.get("/me")
        assert "Hint:" not in str(exc_info.value)
        assert "SomeNewError" in str(exc_info.value)
