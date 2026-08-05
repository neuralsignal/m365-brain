"""Chunked PUT against a pre-authenticated upload-session URL.

Two properties matter and neither is about chunk arithmetic: the bearer token
must not travel to a URL that came out of a response body, and a rejected chunk
must stop the upload rather than leave a partial item looking finished.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from m365_brain.m365.errors import GraphApiError
from m365_brain.m365.upload import upload_in_chunks

SESSION = "https://tenant.sharepoint.com/_api/upload/session-1"


@respx.mock
def test_splits_content_and_reports_each_range():
    route = respx.put(SESSION).mock(
        side_effect=[
            httpx.Response(202, json={"nextExpectedRanges": ["4-"]}),
            httpx.Response(201, json={"eTag": '"final"'}),
        ]
    )

    response = upload_in_chunks(SESSION, b"abcdefg", 4, 5, 200)

    assert response.json()["eTag"] == '"final"'
    ranges = [call.request.headers["Content-Range"] for call in route.calls]
    assert ranges == ["bytes 0-3/7", "bytes 4-6/7"]
    assert [call.request.content for call in route.calls] == [b"abcd", b"efg"]


@respx.mock
def test_no_authorization_header_reaches_the_session_url():
    """The URL is pre-authenticated and came from a response body; the token stays home."""
    route = respx.put(SESSION).mock(return_value=httpx.Response(201, json={}))

    upload_in_chunks(SESSION, b"abc", 10, 5, 200)

    assert "Authorization" not in route.calls[0].request.headers


@respx.mock
def test_a_rejected_chunk_stops_the_upload():
    route = respx.put(SESSION).mock(
        side_effect=[
            httpx.Response(202, json={}),
            httpx.Response(507, text="insufficient storage"),
            httpx.Response(201, json={}),
        ]
    )

    with pytest.raises(GraphApiError) as excinfo:
        upload_in_chunks(SESSION, b"abcdefghi", 3, 5, 200)

    assert excinfo.value.status_code == 507
    assert route.call_count == 2, "the third chunk must not be sent after a failure"


def test_empty_content_is_a_loud_error_not_a_silent_success():
    with pytest.raises(GraphApiError) as excinfo:
        upload_in_chunks(SESSION, b"", 4, 5, 200)

    assert "empty content" in str(excinfo.value)


def test_a_session_url_outside_the_allowed_domains_is_refused():
    with pytest.raises(GraphApiError) as excinfo:
        upload_in_chunks("https://evil.example.com/upload", b"abc", 4, 5, 200)

    assert "blocked" in str(excinfo.value)
