"""Every binary an extractor writes appears in the file catalog. End to end.

This is the defect the catalog exists to fix, and a unit test that inserts a
row by hand cannot see it: the predecessor's `upsert_catalog_entry` was
correct, tested, and never called, so five read scripts queried an empty table
for years. What was missing was not the function but the *call*, and only a run
of the real extractors over real storage can tell you whether it happens.

So: run each extractor that downloads binaries against recorded Graph
responses, through `sync.run_one` -- the wiring is part of what is under test,
not something the test reproduces -- over a real `LocalBackend` with a spy in
front of it. Then compare the rows in the catalog against the bytes the spy
saw. Count, path, size and extension, all four, because three of them can be
right while the fourth is quietly wrong.

The three binary write points this covers:

  * `_attachment_helpers.download_attachments`   -- email attachments
  * `_teams_attachment_helpers._resolve_attachment` -- Teams file attachments
  * `_teams_hosted_content.download_inline_images`  -- Teams inline images

They are the complete set: `write_bytes` appears nowhere else under
`m365_brain/`, and `test_no_other_write_point_exists` keeps it that way.
"""

from __future__ import annotations

import base64
import re
from pathlib import Path

import pytest
from pytest_httpx import HTTPXMock

from m365_brain.config import EmailExtractorConfig, MailboxConfig, TeamsChatsExtractorConfig
from m365_brain.index.backends import create_index_backend
from m365_brain.index.catalog import FileCatalog
from m365_brain.index.catalog_extract import CatalogConversionError, extract_pending
from m365_brain.m365.client import GraphClient
from m365_brain.manifest import ChangeRecorder, RecordingStorage
from m365_brain.model import CatalogQuery
from m365_brain.storage.local import LocalBackend
from m365_brain.sync import build_context, run_one

PDF_BYTES = b"%PDF-1.4 quarterly figures"
TXT_BYTES = b"plain text attachment"
DOCX_BYTES = b"PK\x03\x04 specification"
PNG_BYTES = b"\x89PNG\r\n\x1a\n inline"

CHAT_ID = "19:chat-1"
MESSAGE_ID = "m1"
SHARE_URL = "https://example.com/personal/user/Documents/spec.docx"


class BinarySpy:
    """A `StorageBackend` that remembers the binaries it was asked to write.

    The independent record the catalog is compared against. Asserting the
    catalog against files found on disk would compare it to markdown too, and
    the interesting question is only ever about the bytes.
    """

    def __init__(self, inner: LocalBackend) -> None:
        self._inner = inner
        self.binaries: dict[str, int] = {}

    def write_bytes(self, path: str, content: bytes) -> None:
        self._inner.write_bytes(path, content)
        self.binaries[path] = len(content)

    def write_file(self, path: str, content: str) -> None:
        self._inner.write_file(path, content)

    def read_file(self, path: str) -> str:
        return self._inner.read_file(path)

    def file_exists(self, path: str) -> bool:
        return self._inner.file_exists(path)

    def list_files(self, prefix: str) -> list[str]:
        return self._inner.list_files(prefix)

    def delete_file(self, path: str) -> None:
        self._inner.delete_file(path)


# --------------------------------------------------------------------------
# Recorded Graph responses
# --------------------------------------------------------------------------


def _wire_email(mock: HTTPXMock) -> None:
    mock.add_response(
        url=re.compile(r".*/me/mailFolders/Inbox/messages/delta.*"),
        json={
            "value": [
                {
                    "id": "msg-1",
                    "subject": "Quarterly figures",
                    "body": {"contentType": "text", "content": "See attached."},
                    "from": {"emailAddress": {"name": "Sender", "address": "sender@example.com"}},
                    "toRecipients": [],
                    "receivedDateTime": "2026-03-12T10:00:00Z",
                    "importance": "normal",
                    "hasAttachments": True,
                    "webLink": "",
                    "parentFolderId": "inbox",
                }
            ],
            "@odata.deltaLink": "https://graph.example.com/delta?token=t",
        },
    )
    mock.add_response(
        url=re.compile(r".*/me/messages/msg-1/attachments.*"),
        json={
            "value": [
                {
                    "id": "att-1",
                    "name": "quarterly_figures.pdf",
                    "contentType": "application/pdf",
                    "size": len(PDF_BYTES),
                    "isInline": False,
                    "@microsoft.graph.downloadUrl": "https://attachments.office.com/quarterly_figures.pdf",
                },
                {
                    "id": "att-2",
                    "name": "notes.txt",
                    "contentType": "text/plain",
                    "size": len(TXT_BYTES),
                    "isInline": False,
                    "contentBytes": base64.b64encode(TXT_BYTES).decode("ascii"),
                },
            ]
        },
    )
    mock.add_response(url="https://attachments.office.com/quarterly_figures.pdf", content=PDF_BYTES)


def _wire_teams_chats(mock: HTTPXMock) -> None:
    mock.add_response(
        url=re.compile(r".*/me/chats\?.*"),
        json={
            "value": [
                {
                    "id": CHAT_ID,
                    "chatType": "oneOnOne",
                    "topic": None,
                    "members": [{"displayName": "Alice"}, {"displayName": "Bob"}],
                }
            ]
        },
    )
    mock.add_response(
        url=re.compile(r".*/me/chats/.*/messages.*"),
        json={
            "value": [
                {
                    "id": MESSAGE_ID,
                    "messageType": "message",
                    "createdDateTime": "2026-06-11T09:00:00Z",
                    "lastModifiedDateTime": "2026-06-11T09:00:00Z",
                    "etag": "1",
                    "lastEditedDateTime": None,
                    "deletedDateTime": None,
                    "from": {"user": {"displayName": "Alice", "id": "u1"}},
                    "body": {"contentType": "html", "content": '<img src="../hostedContents/h1/$value">'},
                    "attachments": [
                        {
                            "id": "ref-1",
                            "contentType": "reference",
                            "name": "specification.docx",
                            "contentUrl": SHARE_URL,
                        }
                    ],
                }
            ]
        },
    )
    mock.add_response(
        url=re.compile(rf".*/chats/{re.escape(CHAT_ID)}/messages/{MESSAGE_ID}/hostedContents\?.*"),
        json={"value": [{"id": "h1"}]},
    )
    mock.add_response(
        url=re.compile(r".*/hostedContents/h1/\$value.*"),
        content=PNG_BYTES,
        headers={"Content-Type": "image/png"},
    )
    mock.add_response(
        url=re.compile(r".*/shares/u!.*/driveItem.*"),
        json={
            "id": "drive-item-1",
            "size": len(DOCX_BYTES),
            "@microsoft.graph.downloadUrl": "https://attachments.office.com/specification.docx",
        },
    )
    mock.add_response(url="https://attachments.office.com/specification.docx", content=DOCX_BYTES)


# name -> (wire the responses, the extractor config with downloads switched on,
#          the binaries that run must produce)
CASES = {
    "email": (
        _wire_email,
        EmailExtractorConfig(
            enabled=True,
            poll_interval_minutes=3,
            mailboxes=[MailboxConfig(address="me", folders=["Inbox"], output_subdir="")],
            lookback_days=30,
            max_items_per_sync=100,
            download_attachments=True,
            max_attachment_size_mb=25,
            attachment_convert_extensions=[],
        ),
        {"quarterly_figures.pdf": len(PDF_BYTES), "notes.txt": len(TXT_BYTES)},
    ),
    "teams_chats": (
        _wire_teams_chats,
        TeamsChatsExtractorConfig(
            enabled=True,
            poll_interval_minutes=5,
            max_messages_per_chat=200,
            download_attachments=True,
            download_inline_images=True,
            max_attachment_size_mb=25,
            attachment_convert_extensions=[],
        ),
        {"specification.docx": len(DOCX_BYTES), "inline_0.png": len(PNG_BYTES)},
    ),
}


def _run(name: str, config, httpx_mock: HTTPXMock, tmp_path) -> tuple[BinarySpy, FileCatalog]:
    """Run one extractor through `run_one`; return the spy and an open catalog."""
    wire, extractor_config, _ = CASES[name]
    wire(httpx_mock)
    config = config.model_copy(update={"extractors": config.extractors.model_copy(update={name: extractor_config})})

    spy = BinarySpy(LocalBackend(str(tmp_path / "vault")))
    recorder = ChangeRecorder()
    storage = RecordingStorage(spy, recorder)
    ctx = build_context(config, storage, recorder)
    client = GraphClient(config.graph, lambda: "test-token")
    try:
        run_one(config, client, storage, ctx, {}, name)
    finally:
        client.close()
    return spy, _open_catalog(config)


def _open_catalog(config) -> FileCatalog:
    backend = create_index_backend(config.index)
    backend.initialize()
    return FileCatalog(backend, config.index.catalog)


def _rows(catalog: FileCatalog) -> list:
    return catalog.search(
        CatalogQuery(extension=None, source=None, status=None, modified_after=None, name_contains=None, limit=100)
    )


@pytest.mark.parametrize("name", sorted(CASES))
def test_every_binary_written_becomes_a_catalog_row(name, httpx_mock: HTTPXMock, tmp_path, runtime_config):
    """The whole point: the set of rows equals the set of binaries, exactly."""
    spy, catalog = _run(name, runtime_config, httpx_mock, tmp_path)
    _, _, expected = CASES[name]

    assert spy.binaries, f"{name} wrote no binaries -- the fixture is not exercising the write point"
    rows = _rows(catalog)

    assert {row.original_path: row.size_bytes for row in rows} == spy.binaries
    assert {row.file_name: row.size_bytes for row in rows} == expected


@pytest.mark.parametrize("name", sorted(CASES))
def test_rows_carry_the_extension_the_source_and_the_initial_state(
    name, httpx_mock: HTTPXMock, tmp_path, runtime_config
):
    spy, catalog = _run(name, runtime_config, httpx_mock, tmp_path)
    rows = _rows(catalog)

    assert {row.extension for row in rows} == {Path(path).suffix.lower() for path in spy.binaries}
    assert {row.source for row in rows} == {name}, "source is the extractor that wrote the bytes"
    assert {row.conversion_status for row in rows} == {runtime_config.index.catalog.initial_state}
    assert all(row.output_path is None and row.error is None for row in rows)


@pytest.mark.parametrize("name", sorted(CASES))
def test_the_row_sizes_match_the_files_on_disk(name, httpx_mock: HTTPXMock, tmp_path, runtime_config):
    """`size_bytes` is a fact about a file, so check it against the file."""
    _, catalog = _run(name, runtime_config, httpx_mock, tmp_path)
    root = tmp_path / "vault"

    for row in _rows(catalog):
        assert (root / row.original_path).stat().st_size == row.size_bytes


@pytest.mark.parametrize("name", sorted(CASES))
def test_markdown_is_not_catalogued(name, httpx_mock: HTTPXMock, tmp_path, runtime_config):
    """The markdown index already covers markdown; two indexes over it is one too many."""
    _, catalog = _run(name, runtime_config, httpx_mock, tmp_path)
    assert [row.original_path for row in _rows(catalog) if row.original_path.endswith(".md")] == []


def test_a_second_run_does_not_duplicate_or_reset_rows(httpx_mock: HTTPXMock, tmp_path, runtime_config):
    """Re-downloading an unchanged attachment must not undo its conversion.

    Without the size check in `CatalogingStorage`, the second cycle upserts the
    row back to `pending` and `extract` converts it again on every run, forever.
    """
    _wire_email(httpx_mock)
    _wire_email(httpx_mock)
    config = runtime_config.model_copy(
        update={"extractors": runtime_config.extractors.model_copy(update={"email": CASES["email"][1]})}
    )

    spy = BinarySpy(LocalBackend(str(tmp_path / "vault")))
    recorder = ChangeRecorder()
    storage = RecordingStorage(spy, recorder)
    ctx = build_context(config, storage, recorder)
    client = GraphClient(config.graph, lambda: "test-token")
    try:
        run_one(config, client, storage, ctx, {}, "email")
        catalog = _open_catalog(config)
        first = _rows(catalog)
        catalog.set_status(first[0].original_path, "converted", "somewhere.md", None)

        run_one(config, client, storage, ctx, {}, "email")
    finally:
        client.close()

    second = _rows(_open_catalog(config))
    assert len(second) == len(first)
    converted = next(row for row in second if row.original_path == first[0].original_path)
    assert (converted.conversion_status, converted.output_path) == ("converted", "somewhere.md")


def test_extraction_advances_the_rows_it_converts(httpx_mock: HTTPXMock, tmp_path, runtime_config):
    """Rows land pending, then move to converted with the output path recorded."""
    spy, catalog = _run("email", runtime_config, httpx_mock, tmp_path)
    catalog_config = runtime_config.index.catalog

    def convert(entry) -> str:
        return f"converted/{entry.file_name}.md"

    stats = extract_pending(catalog, catalog_config, convert, limit=100, retry_failed=False)

    assert (stats.attempted, stats.converted, stats.failed) == (len(spy.binaries), len(spy.binaries), 0)
    rows = _rows(catalog)
    assert {row.conversion_status for row in rows} == {catalog_config.converted_state}
    assert all(row.output_path == f"converted/{row.file_name}.md" for row in rows)
    assert all(row.error is None for row in rows)


def test_a_failing_conversion_is_recorded_and_then_skipped(httpx_mock: HTTPXMock, tmp_path, runtime_config):
    """Failure is a terminal state until a caller asks for a retry."""
    _, catalog = _run("email", runtime_config, httpx_mock, tmp_path)
    catalog_config = runtime_config.index.catalog
    attempts: list[str] = []

    def always_fails(entry) -> str:
        attempts.append(entry.original_path)
        raise CatalogConversionError("no converter for this format")

    first = extract_pending(catalog, catalog_config, always_fails, limit=100, retry_failed=False)
    second = extract_pending(catalog, catalog_config, always_fails, limit=100, retry_failed=False)

    assert (first.failed, second.attempted) == (2, 0), "a failed row is not retried on the next run"
    assert len(attempts) == 2
    rows = _rows(catalog)
    assert {row.conversion_status for row in rows} == {catalog_config.failed_state}
    assert all(row.error == "no converter for this format" for row in rows)

    retried = extract_pending(catalog, catalog_config, always_fails, limit=100, retry_failed=True)
    assert retried.attempted == 2, "--retry-failed is the deliberate way back in"


def test_no_other_write_point_exists() -> None:
    """`write_bytes` is called in exactly the three places this file covers.

    A fourth means a new binary producer landed. It is catalogued for free --
    the decorator sits at the boundary, not at the call site -- but the claim
    that this file covers them all needs re-checking, so fail here and say so.
    """
    package = Path(__file__).resolve().parents[4] / "m365_brain"
    callers = sorted(
        path.relative_to(package).as_posix()
        for path in package.rglob("*.py")
        if "__pycache__" not in path.parts and ".write_bytes(" in path.read_text(encoding="utf-8")
    )
    assert callers == [
        "index/catalog_storage.py",
        "m365/extractors/_attachment_helpers.py",
        "m365/extractors/_teams_attachment_helpers.py",
        "m365/extractors/_teams_hosted_content.py",
        "manifest.py",
        "storage/local.py",
    ]
