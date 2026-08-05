"""Drive-item reads, and the write surface that has no unconditional path.

Gate 1 lives here. The claim under test is not "a stale eTag raises" -- that is
easy and insufficient. The claim is "a stale eTag writes nothing", and only the
mock's call log can say so, which is why every conflict test asserts on
`route.call_count` and on the bytes each recorded request carried.

`test_no_public_function_takes_a_nullable_if_match` is the other half: the
guarantee is meant to be structural, so it reads the module's own signatures
rather than trusting that every future caller remembers an argument.
"""

from __future__ import annotations

import inspect
import typing

import httpx
import pytest
import respx

from m365_brain.config import GraphConfig, UploadConfig
from m365_brain.m365 import files as files_module
from m365_brain.m365.client import GRAPH_BASE_URL, GraphClient
from m365_brain.m365.errors import GraphApiError, GraphConflictError, GraphNotFoundError
from m365_brain.m365.files import (
    ETagRequired,
    create_file,
    download_file_bytes,
    encode_path,
    get_file,
    item_etag,
    list_children,
    resolve_default_drive_id,
    resolve_drive_id,
    resolve_site_id,
    update_file,
)

GRAPH = GRAPH_BASE_URL
DRIVE = "drive-1"
ITEM = f"{GRAPH}/drives/{DRIVE}/root:/folder/report.md"


@pytest.fixture()
def client():
    config = GraphConfig(
        max_retries=2,
        backoff_base_ms=0,
        timeout_seconds=5,
        max_pages=10,
        max_retry_after_seconds=300.0,
        error_message_max_length=200,
    )
    with GraphClient(config, lambda: "test-token") as graph:
        yield graph


@pytest.fixture()
def upload():
    """A tiny simple-upload ceiling so the session path is reachable in a test."""
    return UploadConfig(
        inline_attachment_max_bytes=50,
        simple_upload_max_bytes=100,
        chunk_bytes=320 * 1024,
    )


def _not_found(code: str) -> httpx.Response:
    return httpx.Response(404, json={"error": {"code": code, "message": "gone"}})


class TestPathEncoding:
    def test_separators_survive_and_spaces_do_not(self):
        assert encode_path("a folder/report v2.md") == "a%20folder/report%20v2.md"

    def test_non_ascii_is_percent_encoded(self):
        assert encode_path("Dokumente/Übersicht.md") == "Dokumente/%C3%9Cbersicht.md"


class TestResolution:
    @respx.mock
    def test_site_id_uses_colon_addressing(self, client):
        route = respx.get(f"{GRAPH}/sites/contoso.example.com:/sites/Team%20A").mock(
            return_value=httpx.Response(200, json={"id": "site-9"})
        )

        assert resolve_site_id(client, "contoso.example.com", "sites/Team A") == "site-9"
        assert route.called

    @respx.mock
    def test_site_without_an_id_raises(self, client):
        respx.get(url__startswith=f"{GRAPH}/sites/").mock(return_value=httpx.Response(200, json={}))

        with pytest.raises(GraphApiError):
            resolve_site_id(client, "contoso.example.com", "sites/Team")

    @respx.mock
    def test_a_named_library_is_its_own_drive(self, client):
        respx.get(f"{GRAPH}/sites/site-9/drives").mock(
            return_value=httpx.Response(200, json={"value": [{"id": "d-lib", "name": "Reports"}]})
        )

        assert resolve_drive_id(client, "site-9", "Reports") == ("d-lib", False)

    @respx.mock
    def test_an_unnamed_library_falls_back_to_the_default_drive_as_a_folder(self, client):
        respx.get(f"{GRAPH}/sites/site-9/drives").mock(
            return_value=httpx.Response(200, json={"value": [{"id": "d1", "name": "Documents"}]})
        )
        respx.get(f"{GRAPH}/sites/site-9/drive").mock(return_value=httpx.Response(200, json={"id": "d-default"}))

        assert resolve_drive_id(client, "site-9", "Reports") == ("d-default", True)

    @respx.mock
    def test_no_named_and_no_default_drive_names_what_it_saw(self, client):
        respx.get(f"{GRAPH}/sites/site-9/drives").mock(
            return_value=httpx.Response(200, json={"value": [{"id": "d1", "name": "Other"}]})
        )
        respx.get(f"{GRAPH}/sites/site-9/drive").mock(return_value=_not_found("itemNotFound"))

        with pytest.raises(GraphApiError) as excinfo:
            resolve_drive_id(client, "site-9", "Reports")

        assert "'Other'" in str(excinfo.value)

    @respx.mock
    def test_default_drive_id(self, client):
        respx.get(f"{GRAPH}/sites/site-9/drive").mock(return_value=httpx.Response(200, json={"id": "d-default"}))

        assert resolve_default_drive_id(client, "site-9") == "d-default"


class TestListChildren:
    @respx.mock
    def test_follows_next_link_and_flattens_download_urls(self, client):
        route = respx.get(url__startswith=f"{GRAPH}/drives/{DRIVE}/root:/reports:/children").mock(
            side_effect=[
                httpx.Response(
                    200,
                    json={
                        "value": [
                            {
                                "name": "a.md",
                                "id": "1",
                                "lastModifiedDateTime": "2026-01-01T00:00:00Z",
                                "@microsoft.graph.downloadUrl": "https://x.sharepoint.com/a",
                            }
                        ],
                        "@odata.nextLink": f"{GRAPH}/drives/{DRIVE}/root:/reports:/children?skip=1",
                    },
                ),
                httpx.Response(200, json={"value": [{"name": "b.md", "id": "2"}]}),
            ]
        )

        children = list_children(client, DRIVE, "reports")

        assert route.call_count == 2
        assert "skip=1" in str(route.calls[1].request.url)

        assert [c["name"] for c in children] == ["a.md", "b.md"]
        assert children[0]["downloadUrl"] == "https://x.sharepoint.com/a"
        assert children[1]["downloadUrl"] is None

    @respx.mock
    def test_a_missing_folder_raises_rather_than_returning_empty(self, client):
        respx.get(url__startswith=f"{GRAPH}/drives/{DRIVE}/root:").mock(return_value=_not_found("itemNotFound"))

        with pytest.raises(GraphNotFoundError):
            list_children(client, DRIVE, "nope")


class TestReads:
    @respx.mock
    def test_get_file_returns_text_and_etag(self, client):
        respx.get(ITEM).mock(return_value=httpx.Response(200, json={"id": "i", "eTag": '"e1"'}))
        respx.get(f"{ITEM}:/content").mock(return_value=httpx.Response(200, content=b"# hi"))

        assert get_file(client, DRIVE, "folder/report.md") == ("# hi", '"e1"')

    @respx.mock
    def test_get_file_returns_none_for_a_missing_item_and_never_asks_for_content(self, client):
        respx.get(ITEM).mock(return_value=_not_found("itemNotFound"))
        content = respx.get(f"{ITEM}:/content").mock(return_value=httpx.Response(200, content=b"x"))

        assert get_file(client, DRIVE, "folder/report.md") is None
        assert content.call_count == 0

    @respx.mock
    def test_item_etag_is_metadata_only(self, client):
        meta = respx.get(ITEM).mock(return_value=httpx.Response(200, json={"eTag": '"e1"'}))
        content = respx.get(f"{ITEM}:/content").mock(return_value=httpx.Response(200, content=b"x"))

        assert item_etag(client, DRIVE, "folder/report.md") == '"e1"'
        assert meta.call_count == 1
        assert content.call_count == 0

    @respx.mock
    def test_download_file_bytes_raises_on_404(self, client):
        respx.get(f"{ITEM}:/content").mock(return_value=_not_found("itemNotFound"))

        with pytest.raises(GraphNotFoundError):
            download_file_bytes(client, DRIVE, "folder/report.md")


class TestCreateFile:
    @respx.mock
    def test_creates_when_nothing_is_there(self, client, upload):
        respx.get(ITEM).mock(return_value=_not_found("itemNotFound"))
        put = respx.put(f"{ITEM}:/content").mock(return_value=httpx.Response(201, json={"eTag": '"e-new"'}))

        etag = create_file(client, upload, DRIVE, "folder/report.md", b"body", "text/markdown")

        assert etag == '"e-new"'
        assert put.calls[0].request.content == b"body"
        assert "If-Match" not in put.calls[0].request.headers

    @respx.mock
    def test_refuses_an_existing_item_and_writes_nothing(self, client, upload):
        respx.get(ITEM).mock(return_value=httpx.Response(200, json={"eTag": '"e1"'}))
        put = respx.put(f"{ITEM}:/content").mock(return_value=httpx.Response(200, json={"eTag": '"e2"'}))

        with pytest.raises(GraphConflictError) as excinfo:
            create_file(client, upload, DRIVE, "folder/report.md", b"body", "text/markdown")

        assert put.call_count == 0, "create_file must never overwrite"
        assert "update_file" in str(excinfo.value)


class TestUpdateFileIsTheOnlyOverwrite:
    @respx.mock
    def test_sends_if_match_and_returns_the_new_etag(self, client, upload):
        put = respx.put(f"{ITEM}:/content").mock(return_value=httpx.Response(200, json={"eTag": '"e2"'}))

        etag = update_file(client, upload, DRIVE, "folder/report.md", b"new", "text/markdown", '"e1"')

        assert etag == '"e2"'
        assert put.calls[0].request.headers["If-Match"] == '"e1"'

    @respx.mock
    def test_stale_etag_raises_and_writes_nothing(self, client, upload):
        """Gate 1. 'It raised' is not the property; 'it did not write' is."""
        put = respx.put(f"{ITEM}:/content").mock(
            return_value=httpx.Response(412, json={"error": {"code": "preconditionFailed", "message": "etag"}})
        )

        with pytest.raises(GraphConflictError) as excinfo:
            update_file(client, upload, DRIVE, "folder/report.md", b"new", "text/markdown", '"stale"')

        assert excinfo.value.status_code == 412
        assert put.call_count == 1, "a 412 must not be retried into an overwrite"
        accepted = [call for call in put.calls if call.response.status_code < 300]
        assert accepted == [], "no request carrying the new bytes may have been accepted"

    @respx.mock
    def test_an_empty_etag_raises_before_any_request(self, client, upload):
        """Gate 1, second half: zero requests, not merely a failed one."""
        route = respx.put(f"{ITEM}:/content").mock(return_value=httpx.Response(200, json={}))
        meta = respx.get(ITEM).mock(return_value=httpx.Response(200, json={"eTag": '"e1"'}))

        with pytest.raises(ETagRequired):
            update_file(client, upload, DRIVE, "folder/report.md", b"new", "text/markdown", "")

        assert route.call_count == 0
        assert meta.call_count == 0

    def test_no_public_function_takes_a_nullable_if_match(self):
        """The real gate: an unconditional-write path must not exist to be called."""
        public = [
            (name, obj)
            for name, obj in vars(files_module).items()
            if not name.startswith("_") and inspect.isfunction(obj) and obj.__module__ == files_module.__name__
        ]
        assert public, "the module under test exposes no functions"
        for name, function in public:
            hints = typing.get_type_hints(function)
            assert "if_match" not in hints, f"{name} exposes if_match"
            for parameter, annotation in hints.items():
                if parameter == "etag":
                    assert annotation is str, f"{name}.etag must not be optional"


class TestLargeUploads:
    @respx.mock
    def test_content_above_the_ceiling_uses_a_chunked_session(self, client, upload):
        content = b"x" * 300
        respx.post(f"{ITEM}:/createUploadSession").mock(
            return_value=httpx.Response(200, json={"uploadUrl": "https://tenant.sharepoint.com/_api/upload/1"})
        )
        chunks = respx.put("https://tenant.sharepoint.com/_api/upload/1").mock(
            return_value=httpx.Response(201, json={"eTag": '"e-big"'})
        )
        respx.get(ITEM).mock(return_value=_not_found("itemNotFound"))

        etag = create_file(client, upload, DRIVE, "folder/report.md", content, "text/markdown")

        assert etag == '"e-big"'
        assert chunks.calls[0].request.headers["Content-Range"] == "bytes 0-299/300"
        assert "Authorization" not in chunks.calls[0].request.headers

    @respx.mock
    def test_a_moved_etag_stops_the_session_before_it_opens(self, client, upload):
        respx.get(ITEM).mock(return_value=httpx.Response(200, json={"eTag": '"moved"'}))
        session = respx.post(f"{ITEM}:/createUploadSession").mock(
            return_value=httpx.Response(200, json={"uploadUrl": "https://tenant.sharepoint.com/_api/upload/1"})
        )

        with pytest.raises(GraphConflictError):
            update_file(client, upload, DRIVE, "folder/report.md", b"y" * 300, "text/markdown", '"e1"')

        assert session.call_count == 0
