"""The conversion state machine: batching, recording, and not looping.

The failure policy is the part worth pinning. A converter that cannot handle a
format fails every time, so a pipeline that retries failures is a pipeline that
does the same doomed work on every invocation and never reaches the rows behind
it. Failed is terminal until `retry_failed` says otherwise, and both halves of
that are asserted here.
"""

from __future__ import annotations

import pytest

from m365_brain.config.index import IndexConfig
from m365_brain.index.backends.memory import InMemoryIndexBackend
from m365_brain.index.catalog import FileCatalog
from m365_brain.index.catalog_extract import (
    CatalogConversionError,
    converted_output_path,
    extract_pending,
    pending_batch,
)

from .conftest import a_catalog_entry


@pytest.fixture()
def catalog(index_config) -> FileCatalog:
    backend = InMemoryIndexBackend(index_config)
    backend.initialize()
    return FileCatalog(backend, index_config.catalog)


@pytest.fixture()
def catalog_config(index_config):
    return index_config.catalog


def _stock(catalog: FileCatalog, count: int) -> list[str]:
    """`count` pending rows, newest first -- the order a batch comes back in."""
    paths = []
    for number in range(count):
        path = f"inbox/emails/item-{number}/attachments/file-{number}.pdf"
        catalog.upsert(
            a_catalog_entry(
                original_path=path,
                file_name=f"file-{number}.pdf",
                extension=".pdf",
                modified_at=f"2026-03-{number + 1:02d}T00:00:00Z",
            )
        )
        paths.append(path)
    return paths


def test_a_pass_over_an_empty_catalog_does_nothing(catalog, catalog_config):
    stats = extract_pending(catalog, catalog_config, lambda entry: "never.md", limit=10, retry_failed=False)
    assert (stats.attempted, stats.converted, stats.failed) == (0, 0, 0)


def test_success_records_the_output_path_and_clears_a_stale_error(catalog, catalog_config):
    """A row that failed, was re-downloaded, and now converts keeps no old error."""
    path = "inbox/emails/item-0/attachments/file-0.pdf"
    catalog.upsert(
        a_catalog_entry(
            original_path=path,
            file_name="file-0.pdf",
            extension=".pdf",
            conversion_status=catalog_config.failed_state,
            error="an older failure",
        )
    )
    catalog.upsert(a_catalog_entry(original_path=path, file_name="file-0.pdf", extension=".pdf"))

    stats = extract_pending(catalog, catalog_config, lambda entry: "out/file-0.md", limit=10, retry_failed=False)

    entry = catalog.get(path)
    assert stats.converted == 1
    assert (entry.conversion_status, entry.output_path, entry.error) == (
        catalog_config.converted_state,
        "out/file-0.md",
        None,
    )


def test_failure_records_the_message_and_no_output(catalog, catalog_config):
    _stock(catalog, 1)

    def boom(entry) -> str:
        raise CatalogConversionError("DocumentConversionError: encrypted PDF")

    stats = extract_pending(catalog, catalog_config, boom, limit=10, retry_failed=False)

    entry = catalog.get("inbox/emails/item-0/attachments/file-0.pdf")
    assert stats.failed == 1
    assert (entry.conversion_status, entry.output_path, entry.error) == (
        catalog_config.failed_state,
        None,
        "DocumentConversionError: encrypted PDF",
    )


def test_the_limit_caps_the_batch_and_the_rest_waits(catalog, catalog_config):
    _stock(catalog, 5)

    first = extract_pending(catalog, catalog_config, lambda e: "out.md", limit=2, retry_failed=False)
    second = extract_pending(catalog, catalog_config, lambda e: "out.md", limit=2, retry_failed=False)

    assert (first.attempted, second.attempted) == (2, 2)
    assert catalog.stats()[catalog_config.initial_state] == 1


def test_a_failed_row_is_not_picked_up_again(catalog, catalog_config):
    _stock(catalog, 2)

    def boom(entry) -> str:
        raise CatalogConversionError("no")

    extract_pending(catalog, catalog_config, boom, limit=10, retry_failed=False)
    assert pending_batch(catalog, catalog_config, limit=10, retry_failed=False) == []


def test_retry_failed_puts_them_back_in_the_batch(catalog, catalog_config):
    _stock(catalog, 2)

    def boom(entry) -> str:
        raise CatalogConversionError("no")

    extract_pending(catalog, catalog_config, boom, limit=10, retry_failed=False)
    recovered = extract_pending(catalog, catalog_config, lambda e: "out.md", limit=10, retry_failed=True)

    assert (recovered.attempted, recovered.converted) == (2, 2)
    assert catalog.stats()[catalog_config.failed_state] == 0


def test_pending_work_comes_before_retried_work(catalog, catalog_config):
    """A small limit must still make progress on rows nobody has tried."""
    _stock(catalog, 1)
    failed_path = "inbox/emails/old/attachments/old.pdf"
    catalog.upsert(a_catalog_entry(original_path=failed_path, file_name="old.pdf", extension=".pdf"))
    catalog.set_status(failed_path, catalog_config.failed_state, None, "no")

    batch = pending_batch(catalog, catalog_config, limit=1, retry_failed=True)

    assert [entry.original_path for entry in batch] == ["inbox/emails/item-0/attachments/file-0.pdf"]


def test_a_mixed_batch_reports_both_outcomes(catalog, catalog_config):
    _stock(catalog, 2)

    def half(entry) -> str:
        if entry.file_name.endswith("0.pdf"):
            raise CatalogConversionError("no")
        return "out.md"

    stats = extract_pending(catalog, catalog_config, half, limit=10, retry_failed=False)
    assert (stats.attempted, stats.converted, stats.failed) == (2, 1, 1)


@pytest.mark.parametrize(
    ("original", "expected"),
    [
        (
            "inbox/emails/2026/note/attachments/report.pdf",
            "inbox/emails/2026/note/attachments_converted/report.pdf.md",
        ),
        (
            "inbox/teams-chats/chat-1/attachments/m1/inline_0.png",
            "inbox/teams-chats/chat-1/attachments_converted/m1/inline_0.png.md",
        ),
    ],
)
def test_the_markdown_lands_in_the_converted_sibling(original, expected):
    """The same path the eager converters write to, derived rather than copied."""
    assert converted_output_path(original, "attachments", "attachments_converted") == expected


def test_the_directory_names_come_from_the_caller():
    """A renamed vault layout moves the output with it."""
    assert (
        converted_output_path("inbox/mail/x/files/a.pdf", "files", "files-as-text")
        == "inbox/mail/x/files-as-text/a.pdf.md"
    )


def test_a_binary_outside_an_attachments_directory_raises():
    """A new write path with no home for its markdown is worth a crash."""
    with pytest.raises(ValueError, match="not under a 'attachments' directory"):
        converted_output_path("inbox/onedrive/report.pdf", "attachments", "attachments_converted")


def test_the_index_config_round_trips_the_named_states(index_payload):
    """`extract` reads all three state names from config, never a literal."""
    index_payload["catalog"] = {
        "conversion_states": ["queued", "done", "broken"],
        "initial_state": "queued",
        "converted_state": "done",
        "failed_state": "broken",
    }
    config = IndexConfig.model_validate(index_payload)
    backend = InMemoryIndexBackend(config)
    backend.initialize()
    catalog = FileCatalog(backend, config.catalog)
    catalog.upsert(a_catalog_entry(conversion_status="queued"))

    extract_pending(catalog, config.catalog, lambda e: "out.md", limit=10, retry_failed=False)

    assert catalog.get("drive/report.docx").conversion_status == "done"
