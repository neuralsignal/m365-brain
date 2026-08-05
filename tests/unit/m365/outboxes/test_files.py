"""The file executor: a two-way branch on `etag`, and nothing else.

The interesting assertion is the last one -- that a stale eTag reaches the
runner as a 412 rather than as an overwrite. Everything above it is the routing
that makes an unconditional write inexpressible.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from m365_brain.m365.errors import GraphConflictError
from m365_brain.m365.outboxes.files import FileIntentError, FileUpdateOutbox
from m365_brain.vault.dispatch import GraphOp

from .conftest import parse

GRAPH = "https://graph.microsoft.com/v1.0"
# The library resolves to its own drive in most cases here, so the item path
# is the payload's -- `Reports/` only reappears when the library is a folder.
ITEM = f"{GRAPH}/drives/d-1/root:/q3.md"

FILE = {
    "kind": "file.update",
    "site_hostname": "contoso.example.com",
    "site_path": "sites/Team",
    "library_name": "Reports",
    "item_path": "q3.md",
    "etag": None,
    "content_type": "text/markdown",
}

DRAFT = {
    "kind": "email.draft",
    "mailbox": "me",
    "to": ["a@example.com"],
    "cc": None,
    "bcc": None,
    "subject": "Hello",
    "attachments": None,
    "inline_images": None,
    "include_signature": True,
    "revises_message_id": None,
}


@pytest.fixture()
def outbox(client, upload):
    return FileUpdateOutbox(name="file.update", client=client, upload=upload)


def _resolution(library_is_folder: bool) -> None:
    respx.get(url__startswith=f"{GRAPH}/sites/contoso.example.com:").mock(
        return_value=httpx.Response(200, json={"id": "site-1"})
    )
    drives = [] if library_is_folder else [{"id": "d-1", "name": "Reports"}]
    respx.get(f"{GRAPH}/sites/site-1/drives").mock(return_value=httpx.Response(200, json={"value": drives}))
    respx.get(f"{GRAPH}/sites/site-1/drive").mock(return_value=httpx.Response(200, json={"id": "d-1"}))


@respx.mock
def test_a_null_etag_creates(outbox):
    _resolution(library_is_folder=False)
    respx.get(ITEM).mock(return_value=httpx.Response(404, json={"error": {"code": "itemNotFound", "message": "x"}}))
    put = respx.put(f"{ITEM}:/content").mock(return_value=httpx.Response(201, json={"eTag": '"e-new"'}))

    result = outbox.execute(parse("u1", FILE, "# Q3"))

    assert result.graph_message_id == '"e-new"'
    assert put.calls[0].request.content == b"# Q3"
    assert "If-Match" not in put.calls[0].request.headers


@respx.mock
def test_an_etag_updates_conditionally(outbox):
    _resolution(library_is_folder=False)
    put = respx.put(f"{ITEM}:/content").mock(return_value=httpx.Response(200, json={"eTag": '"e2"'}))

    result = outbox.execute(parse("u1", {**FILE, "etag": '"e1"'}, "# Q3"))

    assert result.graph_message_id == '"e2"'
    assert put.calls[0].request.headers["If-Match"] == '"e1"'


@respx.mock
def test_a_library_that_is_a_folder_gets_its_name_back_on_the_path(outbox):
    """A named library is its own drive; one created as a folder lives inside
    the default drive and the caller has to prepend its name."""
    _resolution(library_is_folder=True)
    respx.get(url__startswith=f"{GRAPH}/drives/d-1/root:/Reports/q3.md").mock(
        return_value=httpx.Response(404, json={"error": {"code": "itemNotFound", "message": "x"}})
    )
    put = respx.put(f"{GRAPH}/drives/d-1/root:/Reports/q3.md:/content").mock(
        return_value=httpx.Response(201, json={"eTag": '"e"'})
    )

    outbox.execute(parse("u1", FILE, "# Q3"))

    assert put.called


@respx.mock
def test_a_stale_etag_surfaces_as_a_conflict_and_writes_nothing(outbox):
    _resolution(library_is_folder=False)
    put = respx.put(f"{ITEM}:/content").mock(
        return_value=httpx.Response(412, json={"error": {"code": "preconditionFailed", "message": "etag"}})
    )

    with pytest.raises(GraphConflictError) as excinfo:
        outbox.execute(parse("u1", {**FILE, "etag": '"stale"'}, "# Q3"))

    assert excinfo.value.status_code == 412, "the runner classifies on this, not on the class name"
    assert put.call_count == 1


@respx.mock
def test_creating_over_an_existing_item_is_refused(outbox):
    _resolution(library_is_folder=False)
    respx.get(ITEM).mock(return_value=httpx.Response(200, json={"eTag": '"e1"'}))
    put = respx.put(f"{ITEM}:/content").mock(return_value=httpx.Response(200, json={}))

    with pytest.raises(GraphConflictError):
        outbox.execute(parse("u1", FILE, "# Q3"))

    assert put.call_count == 0


def test_a_payload_of_the_wrong_kind_is_refused(outbox):
    with pytest.raises(FileIntentError):
        outbox.execute(parse("u1", DRAFT))


def test_it_declares_the_file_write_operation(outbox):
    assert outbox.declared_ops == frozenset({GraphOp.PUT_FILE})
