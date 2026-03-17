"""Tests for Graph API client."""

from __future__ import annotations

import httpx
import pytest
from pytest_httpx import HTTPXMock

from m365_extract.config import GraphConfig
from m365_extract.graph_client import GRAPH_BASE_URL, GraphClient


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
        with pytest.raises(httpx.HTTPStatusError):
            client.get("/me")

    def test_404_raises_immediately(self, httpx_mock: HTTPXMock, client):
        httpx_mock.add_response(
            url=f"{GRAPH_BASE_URL}/me",
            status_code=404,
        )
        with pytest.raises(httpx.HTTPStatusError):
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
        with pytest.raises(httpx.HTTPStatusError):
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
