"""Chunked PUT against a Graph upload-session URL.

Deliberately not a `GraphClient` method. An upload-session URL is
*pre-authenticated*: Graph hands it back inside a response body and it carries
its own credential in the query string. Sending our bearer token to it would
hand a Microsoft-issued access token to a host we did not choose, and the
per-chunk `Content-Range` header has no place on the general transport either.
So this is a second, smaller transport with one job and no `Authorization`
header at all.

The URL still passes the download-domain guard before anything is sent to it,
for the same reason `get_bytes` applies it: the host came from a response body.
"""

from __future__ import annotations

import httpx
import structlog

from m365_brain.m365.errors import GraphApiError
from m365_brain.m365.graph_helpers import validated_download_ref

log = structlog.get_logger()

# Graph answers 202 for every chunk but the last, and 200 or 201 for the last
# one depending on whether the item already existed.
UPLOAD_CHUNK_OK_STATUSES = frozenset({200, 201, 202})


def upload_in_chunks(
    url: str,
    content: bytes,
    chunk_bytes: int,
    timeout_seconds: int,
    error_message_max_length: int,
) -> httpx.Response:
    """PUT `content` to an upload-session URL in `chunk_bytes` slices.

    Returns the final chunk's response, which carries the created item. Raises
    `GraphApiError` on the first chunk Graph does not accept -- there is no
    resume logic, because a failed upload session is abandoned and re-opened
    rather than repaired, and pretending otherwise would leave a half-written
    item looking like a success.
    """
    log_ref = validated_download_ref(url)
    total = len(content)
    offset = 0
    response: httpx.Response | None = None
    with httpx.Client(timeout=timeout_seconds) as client:
        while offset < total:
            end = min(offset + chunk_bytes, total) - 1
            chunk = content[offset : end + 1]
            response = client.put(
                url,
                content=chunk,
                headers={"Content-Range": f"bytes {offset}-{end}/{total}"},
            )
            if response.status_code not in UPLOAD_CHUNK_OK_STATUSES:
                raise GraphApiError(
                    f"upload session chunk {offset}-{end}/{total} rejected on {log_ref}: "
                    f"HTTP {response.status_code}: {response.text[:error_message_max_length]}",
                    response.status_code,
                )
            offset = end + 1

    if response is None:
        raise GraphApiError(f"upload session opened for empty content: {log_ref}", None)
    log.info("graph.upload_session_complete", path=log_ref, bytes=total)
    return response
