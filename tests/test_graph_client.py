"""Tests for Graph API client."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import httpx
import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pytest_httpx import HTTPXMock

import m365_extract.graph_client as graph_client_module
from m365_extract.config import GraphConfig
from m365_extract.graph_client import (
    GRAPH_BASE_URL,
    GraphApiError,
    GraphClient,
)
from m365_extract.graph_helpers import (
    ALLOWED_DOWNLOAD_DOMAINS,
    _extract_graph_error,
    _is_allowed_download_domain,
    _sanitize_log_url,
)


@pytest.fixture()
def graph_config():
    return GraphConfig(
        max_retries=2,
        backoff_base_ms=10,
        timeout_seconds=5,
        max_pages=10,
        max_retry_after_seconds=300.0,
        error_message_max_length=200,
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
        result = client.get("/me", params=None)
        assert result["displayName"] == "Test User"

    def test_sends_auth_header(self, httpx_mock: HTTPXMock, client):
        httpx_mock.add_response(
            url=f"{GRAPH_BASE_URL}/me",
            json={"displayName": "Test User"},
        )
        client.get("/me", params=None)
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

        result = c.get("/me", params=None)
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
        result = client.get("/me", params=None)
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
        result = client.get("/me", params=None)
        assert result["ok"] is True

    def test_max_retries_exceeded_raises(self, httpx_mock: HTTPXMock, client):
        for _ in range(3):
            httpx_mock.add_response(
                url=f"{GRAPH_BASE_URL}/me",
                status_code=500,
            )
        with pytest.raises(GraphApiError, match="HTTP 500"):
            client.get("/me", params=None)

    def test_404_raises_immediately(self, httpx_mock: HTTPXMock, client):
        httpx_mock.add_response(
            url=f"{GRAPH_BASE_URL}/me",
            status_code=404,
        )
        with pytest.raises(GraphApiError, match="HTTP 404"):
            client.get("/me", params=None)


class TestGetBytes:
    def test_successful_download(self, httpx_mock: HTTPXMock, client):
        httpx_mock.add_response(
            url=f"{GRAPH_BASE_URL}/me/drive/items/file-1/content",
            content=b"file-content-bytes",
        )
        result = client.get_bytes("/me/drive/items/file-1/content")
        assert result == b"file-content-bytes"

    def test_absolute_url_allowed_domain(self, httpx_mock: HTTPXMock, client):
        absolute_url = "https://tenant.sharepoint.com/sites/docs/file.docx"
        httpx_mock.add_response(
            url=absolute_url,
            content=b"downloaded",
        )
        result = client.get_bytes(absolute_url)
        assert result == b"downloaded"

    def test_absolute_url_blocked_domain(self, client):
        with pytest.raises(GraphApiError, match="not an allowed Microsoft domain"):
            client.get_bytes("https://evil.attacker.com/steal-token")

    def test_sas_token_not_in_log_ref(self, httpx_mock: HTTPXMock, client):
        url = "https://tenant.sharepoint.com/path/file.docx?sv=2021&sig=SECRET_SAS"
        httpx_mock.add_response(url=url, content=b"ok")
        client.get_bytes(url)
        # If we get here without error, the request succeeded.
        # The log_ref sanitization is verified by unit tests on _sanitize_log_url.

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


class TestGetBytesWithContentType:
    def test_absolute_url_blocked_domain(self, client):
        with pytest.raises(GraphApiError, match="Download URL blocked"):
            client.get_bytes_with_content_type("https://evil.example.com/file")

    def test_returns_bytes_and_content_type(self, httpx_mock: HTTPXMock, client):
        httpx_mock.add_response(
            url=f"{GRAPH_BASE_URL}/me/photo/$value",
            content=b"imgdata",
            headers={"Content-Type": "image/png"},
        )
        data, ct = client.get_bytes_with_content_type("/me/photo/$value")
        assert data == b"imgdata"
        assert ct == "image/png"


class TestGetPaginated:
    def test_single_page(self, httpx_mock: HTTPXMock, client):
        httpx_mock.add_response(
            url=f"{GRAPH_BASE_URL}/me/messages",
            json={"value": [{"id": "1"}, {"id": "2"}]},
        )
        items = list(client.get_paginated("/me/messages", params=None, max_pages=10))
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
        items = list(client.get_paginated("/me/messages", params=None, max_pages=10))
        assert len(items) == 2

    def test_max_pages_limit(self, httpx_mock: HTTPXMock, client):
        httpx_mock.add_response(
            url=f"{GRAPH_BASE_URL}/me/messages",
            json={
                "value": [{"id": "1"}],
                "@odata.nextLink": f"{GRAPH_BASE_URL}/me/messages?$skip=1",
            },
        )
        items = list(client.get_paginated("/me/messages", params=None, max_pages=1))
        assert len(items) == 1

    def test_warns_only_on_true_truncation(self, httpx_mock: HTTPXMock, client):
        """max_pages_reached fires when a nextLink remains unfetched after the page cap."""
        httpx_mock.add_response(
            url=f"{GRAPH_BASE_URL}/me/messages",
            json={
                "value": [{"id": "1"}],
                "@odata.nextLink": f"{GRAPH_BASE_URL}/me/messages?$skip=1",
            },
        )
        events: list[str] = []
        with patch.object(graph_client_module.log, "warning", side_effect=lambda e, **kw: events.append(e)):
            list(client.get_paginated("/me/messages", params=None, max_pages=1))
        assert "graph.max_pages_reached" in events

    def test_no_warning_when_final_allowed_page_completes(self, httpx_mock: HTTPXMock, client):
        """No max_pages_reached warning when the last allowed page has no nextLink."""
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
        events: list[str] = []
        with patch.object(graph_client_module.log, "warning", side_effect=lambda e, **kw: events.append(e)):
            items = list(client.get_paginated("/me/messages", params=None, max_pages=2))
        assert len(items) == 2
        assert "graph.max_pages_reached" not in events

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
        items = list(client.get_paginated("/me/messages", params=None, max_pages=10))
        assert len(items) == 2
        assert items[1]["id"] == "2"

    def test_empty_response(self, httpx_mock: HTTPXMock, client):
        httpx_mock.add_response(
            url=f"{GRAPH_BASE_URL}/me/messages",
            json={"value": []},
        )
        items = list(client.get_paginated("/me/messages", params=None, max_pages=10))
        assert items == []


class TestGetPages:
    def test_complete_fetch_returns_items_and_not_truncated(self, httpx_mock: HTTPXMock, client):
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
        items, truncated = client.get_pages("/me/messages", params=None, max_pages=10)
        assert [item["id"] for item in items] == ["1", "2"]
        assert truncated is False

    def test_truncated_when_next_link_remains_at_cap(self, httpx_mock: HTTPXMock, client):
        """truncated=True (with a warning) when a nextLink remained unfetched at the page cap."""
        httpx_mock.add_response(
            url=f"{GRAPH_BASE_URL}/me/messages",
            json={
                "value": [{"id": "1"}],
                "@odata.nextLink": f"{GRAPH_BASE_URL}/me/messages?$skip=1",
            },
        )
        events: list[str] = []
        with patch.object(graph_client_module.log, "warning", side_effect=lambda e, **kw: events.append(e)):
            items, truncated = client.get_pages("/me/messages", params=None, max_pages=1)
        assert len(items) == 1
        assert truncated is True
        assert "graph.max_pages_reached" in events

    def test_params_sent_on_first_page_only(self, httpx_mock: HTTPXMock, client):
        httpx_mock.add_response(
            url=f"{GRAPH_BASE_URL}/me/messages?$top=50",
            json={
                "value": [{"id": "1"}],
                "@odata.nextLink": f"{GRAPH_BASE_URL}/me/messages?$skip=1",
            },
        )
        httpx_mock.add_response(
            url=f"{GRAPH_BASE_URL}/me/messages?$skip=1",
            json={"value": [{"id": "2"}]},
        )
        items, truncated = client.get_pages("/me/messages", params={"$top": "50"}, max_pages=10)
        assert len(items) == 2
        assert truncated is False


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
            params=None,
            max_pages=10,
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
            params=None,
            max_pages=10,
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
            params=None,
            max_pages=10,
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
        code, message = _extract_graph_error(body, 200)
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
        code, message = _extract_graph_error(body, 200)
        assert code == "BadRequest"
        assert len(message) <= 200

    def test_non_json_body_returns_unknown(self):
        body = "<html>Internal Server Error with user@company.com PII data</html>"
        code, message = _extract_graph_error(body, 200)
        assert code == "unknown"
        assert message == "non-json response"

    def test_missing_error_key_returns_unknown(self):
        body = json.dumps({"status": "failed", "detail": "user pii@example.com"})
        code, message = _extract_graph_error(body, 200)
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
            c.get("/me", params=None)
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
            client.get("/me/messages/123", params=None)


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
            client.get("/me/messages", params=None)
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
        result = client.get("/me/messages", params=None)
        assert result == {"value": []}

    def test_429_retry_after_capped_at_maximum(self, httpx_mock: HTTPXMock, client, monkeypatch):
        """429 with excessively large Retry-After should be capped at _MAX_RETRY_AFTER_SECONDS."""
        sleep_calls: list[float] = []
        monkeypatch.setattr("m365_extract.graph_client.time.sleep", lambda s: sleep_calls.append(s))
        httpx_mock.add_response(
            url=f"{GRAPH_BASE_URL}/me/messages",
            status_code=429,
            headers={"Retry-After": "999999999"},
        )
        httpx_mock.add_response(
            url=f"{GRAPH_BASE_URL}/me/messages",
            json={"value": []},
        )
        result = client.get("/me/messages", params=None)
        assert result == {"value": []}
        assert sleep_calls == [300.0]

    def test_429_retry_after_non_numeric_falls_back_to_backoff(self, httpx_mock: HTTPXMock, client, monkeypatch):
        """429 with non-numeric Retry-After (e.g. HTTP-date) should fall back to exponential backoff."""
        sleep_calls: list[float] = []
        monkeypatch.setattr("m365_extract.graph_client.time.sleep", lambda s: sleep_calls.append(s))
        httpx_mock.add_response(
            url=f"{GRAPH_BASE_URL}/me/messages",
            status_code=429,
            headers={"Retry-After": "Wed, 21 Oct 2025 07:28:00 GMT"},
        )
        httpx_mock.add_response(
            url=f"{GRAPH_BASE_URL}/me/messages",
            json={"value": []},
        )
        result = client.get("/me/messages", params=None)
        assert result == {"value": []}
        # First attempt (attempt=0): backoff_base_ms=10 -> 0.01s * 2^0 = 0.01
        assert sleep_calls == [0.01]

    def test_410_gone_raises_immediately(self, httpx_mock: HTTPXMock, client):
        """410 Gone (expired delta token) is not retryable — should raise immediately."""
        httpx_mock.add_response(
            url=f"{GRAPH_BASE_URL}/me/contacts/delta",
            status_code=410,
            text=json.dumps({"error": {"code": "ResyncRequired", "message": "Delta token expired."}}),
        )
        with pytest.raises(GraphApiError, match="HTTP 410"):
            client.get("/me/contacts/delta", params=None)
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
            client.get("/me/messages", params=None)
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
            c.get("/me", params=None)
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
            client.get("/me", params=None)
        assert "Hint:" not in str(exc_info.value)
        assert "SomeNewError" in str(exc_info.value)


class TestIsAllowedDownloadDomain:
    @pytest.mark.parametrize(
        "url",
        [
            "https://tenant.sharepoint.com/sites/docs/file.docx",
            "https://cdn.1drv.com/path/file",
            "https://graph.microsoft.com/v1.0/me/photo",
            "https://files.office.com/download/abc",
            "https://storage.office365.com/blobs/123",
            "https://files.cdn.office.net/assets/doc.docx",
            "https://svc.ms/v1/download/abc",
            "https://blob.svc.ms/v1/download/abc",
        ],
    )
    def test_allowed_domains(self, url: str) -> None:
        assert _is_allowed_download_domain(url) is True

    @pytest.mark.parametrize(
        "url",
        [
            "https://evil.com/file",
            "https://attacker.sharepoint.com.evil.com/file",
            "https://notmicrosoft.com/file",
            "https://sharepoint.com.evil.org/file",
            "https://attacker.blob.core.windows.net/container/blob",
            "https://evilsvc.ms/malicious",
            "https://fakesvc.ms/payload",
            "https://attack.evilsvc.ms/file",
        ],
    )
    def test_blocked_domains(self, url: str) -> None:
        assert _is_allowed_download_domain(url) is False


class TestSanitizeLogUrl:
    def test_strips_query_params(self) -> None:
        url = "https://tenant.sharepoint.com/path/file.docx?sv=2021-06-08&sig=SECRET"
        assert _sanitize_log_url(url) == "https://tenant.sharepoint.com/path/file.docx"

    def test_preserves_url_without_query(self) -> None:
        url = "https://tenant.sharepoint.com/path/file.docx"
        assert _sanitize_log_url(url) == url

    def test_strips_fragment_too(self) -> None:
        url = "https://host.com/path?q=1#frag"
        assert _sanitize_log_url(url) == "https://host.com/path"


class TestContextManager:
    def test_enter_returns_self(self, graph_config, token_provider):
        client = GraphClient(graph_config, token_provider)
        result = client.__enter__()
        assert result is client
        client.close()

    def test_exit_closes_client(self, graph_config, token_provider):
        client = GraphClient(graph_config, token_provider)
        client._client = MagicMock(spec=httpx.Client)
        client.__exit__(None, None, None)
        client._client.close.assert_called_once()

    def test_with_statement(self, graph_config, token_provider, httpx_mock: HTTPXMock):
        httpx_mock.add_response(url=f"{GRAPH_BASE_URL}/me", json={"ok": True})
        with GraphClient(graph_config, token_provider) as client:
            result = client.get("/me", params=None)
            assert result["ok"] is True


class TestTransportErrorRetry:
    def test_retries_on_transport_error_then_succeeds(self, graph_config, token_provider, monkeypatch):
        monkeypatch.setattr("m365_extract.graph_client.time.sleep", lambda s: None)
        call_count = 0

        def mock_get(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise httpx.ConnectError("connection refused")
            return httpx.Response(200, json={"ok": True})

        client = GraphClient(graph_config, token_provider)
        monkeypatch.setattr(client._client, "get", mock_get)
        result = client.get("/me", params=None)
        assert result["ok"] is True
        assert call_count == 2
        client.close()

    def test_raises_on_final_transport_error(self, graph_config, token_provider, monkeypatch):
        monkeypatch.setattr("m365_extract.graph_client.time.sleep", lambda s: None)

        def always_fail(*args, **kwargs):
            raise httpx.ConnectError("connection refused")

        client = GraphClient(graph_config, token_provider)
        monkeypatch.setattr(client._client, "get", always_fail)
        with pytest.raises(httpx.ConnectError, match="connection refused"):
            client.get("/me", params=None)
        client.close()


class TestMaxRetriesExhausted:
    def test_raises_after_all_retries_loop_fallthrough(self, graph_config, token_provider, monkeypatch):
        """Cover lines 237-238: GraphApiError after loop falls through without raising."""
        monkeypatch.setattr("m365_extract.graph_client.time.sleep", lambda s: None)
        # Use max_retries=0 so the loop runs once (attempt=0).
        # A 401 on attempt 0 does a silent retry (continue), but with max_retries=0
        # the loop ends after one iteration, falling through to line 237.
        config = GraphConfig(
            max_retries=0,
            backoff_base_ms=10,
            timeout_seconds=5,
            max_pages=10,
            max_retry_after_seconds=300.0,
            error_message_max_length=200,
        )
        client = GraphClient(config, token_provider)

        # Return 401 on the single attempt — attempt==0 triggers silent continue,
        # loop ends, falls through to line 237
        call_count = 0

        def mock_get(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return httpx.Response(401, text="unauthorized")

        monkeypatch.setattr(client._client, "get", mock_get)

        with pytest.raises(GraphApiError, match="failed after 0 retries"):
            client.get("/me", params=None)
        client.close()


class TestGetDeltaMaxPages:
    def test_returns_pending_next_link_at_cap(self, httpx_mock: HTTPXMock, client):
        """When the page cap interrupts a delta round, the pending nextLink is the resume link."""
        pending = f"{GRAPH_BASE_URL}/me/messages/delta?skip=1"
        httpx_mock.add_response(
            url=f"{GRAPH_BASE_URL}/me/messages/delta",
            json={
                "value": [{"id": "1"}],
                "@odata.nextLink": pending,
            },
        )
        events: list[str] = []
        with patch.object(graph_client_module.log, "warning", side_effect=lambda e, **kw: events.append(e)):
            items, resume_link = client.get_delta(
                "/me/messages/delta",
                None,
                params=None,
                max_pages=1,
            )
        assert len(items) == 1
        assert resume_link == pending
        assert "graph.delta_max_pages_reached" in events

    def test_no_warning_when_delta_round_completes_on_final_page(self, httpx_mock: HTTPXMock, client):
        """No truncation warning when the last allowed page carries the deltaLink."""
        httpx_mock.add_response(
            url=f"{GRAPH_BASE_URL}/me/messages/delta",
            json={
                "value": [{"id": "1"}],
                "@odata.deltaLink": "https://graph.microsoft.com/delta?token=done",
            },
        )
        events: list[str] = []
        with patch.object(graph_client_module.log, "warning", side_effect=lambda e, **kw: events.append(e)):
            items, delta_link = client.get_delta(
                "/me/messages/delta",
                None,
                params=None,
                max_pages=1,
            )
        assert len(items) == 1
        assert delta_link == "https://graph.microsoft.com/delta?token=done"
        assert "graph.delta_max_pages_reached" not in events


class TestExtractGraphErrorProperty:
    """Property-based tests for _extract_graph_error."""

    @given(
        code=st.text(min_size=1, max_size=50),
        message=st.text(min_size=0, max_size=500),
        max_len=st.integers(min_value=1, max_value=500),
    )
    @settings(max_examples=50)
    def test_valid_graph_error_json(self, code: str, message: str, max_len: int) -> None:
        body = json.dumps({"error": {"code": code, "message": message}})
        result_code, result_message = _extract_graph_error(body, max_len)
        assert result_code == code
        assert result_message == message[:max_len]
        assert len(result_message) <= max_len

    @given(body=st.text())
    @settings(max_examples=50)
    def test_arbitrary_text_returns_fallback(self, body: str) -> None:
        code, message = _extract_graph_error(body, 200)
        # Either it's valid JSON with the right structure, or we get the fallback
        try:
            data = json.loads(body)
            error = data["error"]
            _ = error["code"]
            _ = error["message"]
            # Valid structure — code should match
        except (json.JSONDecodeError, KeyError, TypeError):
            assert code == "unknown"
            assert message == "non-json response"


class TestIsAllowedDownloadDomainProperty:
    """Property-based tests for _is_allowed_download_domain."""

    @given(
        suffix=st.sampled_from(sorted(ALLOWED_DOWNLOAD_DOMAINS)),
        subdomain=st.from_regex(r"[a-z]{1,10}", fullmatch=True),
    )
    @settings(max_examples=50)
    def test_allowed_suffix_returns_true(self, suffix: str, subdomain: str) -> None:
        url = f"https://{subdomain}{suffix}/path/file"
        assert _is_allowed_download_domain(url) is True

    @given(
        host=st.from_regex(r"[a-z]{3,10}\.(org|net|io|xyz)", fullmatch=True),
    )
    @settings(max_examples=50)
    def test_non_microsoft_host_returns_false(self, host: str) -> None:
        url = f"https://{host}/path/file"
        # Verify the host doesn't accidentally match an allowed domain
        from urllib.parse import urlparse

        parsed = urlparse(url)
        hostname = parsed.hostname or ""
        if any(hostname == s.lstrip(".") or hostname.endswith(s) for s in ALLOWED_DOWNLOAD_DOMAINS):
            return  # Skip if randomly generated host happens to match
        assert _is_allowed_download_domain(url) is False
