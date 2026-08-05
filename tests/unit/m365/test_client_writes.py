"""The write half of the merged transport: POST, PATCH, PUT and their failures.

Ported from the retry shell that used to live beside the draft sender. Its
policy is now `GraphClient`'s, so these cases pin that the merge kept it: the
same retry/backoff/401-refresh path a GET takes, plus the two statuses that
only a write can produce -- 404 on a vanished target and 412 on a stale eTag.

The 412 case is the load-bearing one. "It raised" is not the property; "it did
not write" is, so the assertions read the mock's call log rather than only the
exception type.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from m365_brain.config import GraphConfig
from m365_brain.m365.client import (
    GRAPH_BASE_URL,
    GraphApiError,
    GraphClient,
    GraphConflictError,
    GraphNotFoundError,
)

GRAPH = GRAPH_BASE_URL


@pytest.fixture()
def write_config():
    """Zero backoff: the retry policy is under test, not the wall clock."""
    return GraphConfig(
        max_retries=3,
        backoff_base_ms=0,
        timeout_seconds=5,
        max_pages=10,
        max_retry_after_seconds=300.0,
        error_message_max_length=200,
    )


@pytest.fixture()
def client(write_config):
    with GraphClient(write_config, lambda: "test-token") as graph:
        yield graph


@respx.mock
def test_post_sends_json_body_and_content_type(client):
    route = respx.post(f"{GRAPH}/me/messages").mock(return_value=httpx.Response(201, json={"id": "AAMk"}))

    response = client.post("/me/messages", {"subject": "hi"})

    assert response.json()["id"] == "AAMk"
    request = route.calls[0].request
    assert request.content == b'{"subject": "hi"}'
    assert request.headers["Content-Type"] == "application/json"
    assert request.headers["Authorization"] == "Bearer test-token"


@respx.mock
def test_bodyless_post_sends_no_content_type(client):
    route = respx.post(f"{GRAPH}/me/messages/AAMk/createReply").mock(
        return_value=httpx.Response(201, json={"id": "reply"})
    )

    client.post("/me/messages/AAMk/createReply", None)

    request = route.calls[0].request
    assert request.content == b""
    assert "Content-Type" not in request.headers


@respx.mock
def test_patch_sends_json_body(client):
    route = respx.patch(f"{GRAPH}/me/messages/AAMk").mock(return_value=httpx.Response(200, json={"id": "AAMk"}))

    client.patch("/me/messages/AAMk", {"subject": "revised"})

    assert route.calls[0].request.content == b'{"subject": "revised"}'


@respx.mock
def test_put_bytes_sends_raw_body_and_content_type(client):
    route = respx.put(f"{GRAPH}/drives/D/root:/f.md:/content").mock(
        return_value=httpx.Response(200, json={"id": "i", "eTag": '"etag-new"'})
    )

    response = client.put_bytes("/drives/D/root:/f.md:/content", b"hello", "text/markdown", None)

    assert response.json()["eTag"] == '"etag-new"'
    request = route.calls[0].request
    assert request.content == b"hello"
    assert request.headers["Content-Type"] == "text/markdown"
    assert "If-Match" not in request.headers


@respx.mock
def test_put_bytes_sets_if_match_header(client):
    route = respx.put(f"{GRAPH}/drives/D/root:/f.md:/content").mock(
        return_value=httpx.Response(200, json={"eTag": '"e2"'})
    )

    client.put_bytes("/drives/D/root:/f.md:/content", b"x", "text/markdown", '"e1"')

    assert route.calls[0].request.headers["If-Match"] == '"e1"'


@respx.mock
def test_put_bytes_accepts_202_from_an_upload_session_chunk(client):
    """Upload-session chunks answer 202 until the last one; 200-only would break them."""
    respx.put(f"{GRAPH}/uploadSession/abc").mock(return_value=httpx.Response(202, json={"nextExpectedRanges": ["5-"]}))

    response = client.put_bytes("/uploadSession/abc", b"chunk", "application/octet-stream", None)

    assert response.status_code == 202


@respx.mock
def test_404_raises_graph_not_found_with_status(client):
    respx.patch(f"{GRAPH}/me/messages/gone").mock(
        return_value=httpx.Response(404, json={"error": {"code": "ErrorItemNotFound", "message": "not found"}})
    )

    with pytest.raises(GraphNotFoundError) as excinfo:
        client.patch("/me/messages/gone", {"subject": "x"})

    assert excinfo.value.status_code == 404
    assert isinstance(excinfo.value, GraphApiError)


@respx.mock
def test_404_is_not_retried(client):
    route = respx.get(f"{GRAPH}/sites/gone").mock(
        return_value=httpx.Response(404, json={"error": {"code": "itemNotFound", "message": "gone"}})
    )

    with pytest.raises(GraphNotFoundError):
        client.get("/sites/gone", params=None)

    assert route.call_count == 1


@respx.mock
def test_412_raises_conflict_and_writes_nothing(client):
    """A stale eTag must never become an overwrite -- assert on the call log."""
    route = respx.put(f"{GRAPH}/drives/D/root:/f.md:/content").mock(
        return_value=httpx.Response(412, json={"error": {"code": "preconditionFailed", "message": "etag"}})
    )

    with pytest.raises(GraphConflictError) as excinfo:
        client.put_bytes("/drives/D/root:/f.md:/content", b"new", "text/markdown", '"stale"')

    assert excinfo.value.status_code == 412
    assert route.call_count == 1, "a 412 must not be retried into an overwrite"
    assert route.calls[0].request.headers["If-Match"] == '"stale"'


@respx.mock
def test_retries_on_429_honouring_retry_after(client):
    route = respx.post(f"{GRAPH}/me/messages").mock(
        side_effect=[
            httpx.Response(429, headers={"Retry-After": "0"}, json={"error": {"code": "TooManyRequests"}}),
            httpx.Response(201, json={"id": "AAMk"}),
        ]
    )

    response = client.post("/me/messages", {"subject": "hi"})

    assert response.json()["id"] == "AAMk"
    assert route.call_count == 2


@respx.mock
def test_retries_on_500_then_succeeds(client):
    route = respx.patch(f"{GRAPH}/me/messages/AAMk").mock(
        side_effect=[
            httpx.Response(500, json={"error": {"code": "InternalServerError"}}),
            httpx.Response(200, json={"id": "AAMk"}),
        ]
    )

    client.patch("/me/messages/AAMk", {"subject": "x"})

    assert route.call_count == 2


@respx.mock
def test_raises_after_exhausting_retries_on_503(write_config):
    respx.post(f"{GRAPH}/me/messages").mock(
        return_value=httpx.Response(503, json={"error": {"code": "ServiceUnavailable", "message": "down"}})
    )

    with GraphClient(write_config, lambda: "test-token") as client, pytest.raises(GraphApiError) as excinfo:
        client.post("/me/messages", {"subject": "hi"})

    assert excinfo.value.status_code == 503


@respx.mock
def test_raises_on_403_without_retrying(client):
    route = respx.post(f"{GRAPH}/me/messages").mock(
        return_value=httpx.Response(403, json={"error": {"code": "accessDenied", "message": "no"}})
    )

    with pytest.raises(GraphApiError) as excinfo:
        client.post("/me/messages", {"subject": "hi"})

    assert excinfo.value.status_code == 403
    assert route.call_count == 1


@respx.mock
def test_401_refreshes_the_token_once_then_succeeds(write_config):
    tokens = iter(["stale", "fresh", "fresh"])
    route = respx.post(f"{GRAPH}/me/messages").mock(
        side_effect=[
            httpx.Response(401, json={"error": {"code": "InvalidAuthenticationToken"}}),
            httpx.Response(201, json={"id": "AAMk"}),
        ]
    )

    with GraphClient(write_config, lambda: next(tokens)) as client:
        response = client.post("/me/messages", {"subject": "hi"})

    assert response.json()["id"] == "AAMk"
    assert route.calls[0].request.headers["Authorization"] == "Bearer stale"
    assert route.calls[1].request.headers["Authorization"] == "Bearer fresh"


@respx.mock
def test_second_401_raises(write_config):
    respx.post(f"{GRAPH}/me/messages").mock(
        return_value=httpx.Response(401, json={"error": {"code": "InvalidAuthenticationToken", "message": "bad"}})
    )

    with GraphClient(write_config, lambda: "stale") as client, pytest.raises(GraphApiError) as excinfo:
        client.post("/me/messages", {"subject": "hi"})

    assert excinfo.value.status_code == 401


def test_client_follows_redirects(client):
    """Graph's /content endpoint 302s to a CDN URL; not following it breaks downloads."""
    assert client._client.follow_redirects is True
