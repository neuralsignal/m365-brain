"""The registration decorator: bytes in, catalog row out, same call.

The end-to-end proof lives in `tests/unit/m365/extractors/test_catalog_registration.py`
-- these are the edges that run is too coarse to show: what happens on a second
write, what a markdown write must *not* do, and what the wrapper does when
there is no index configured at all.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from m365_brain.config.index import IndexConfig
from m365_brain.index.backends.memory import InMemoryIndexBackend
from m365_brain.index.catalog import FileCatalog
from m365_brain.index.catalog_storage import CatalogingStorage, catalog_writes
from m365_brain.storage.local import LocalBackend

FIXED_NOW = datetime(2026, 3, 12, 10, 30, tzinfo=UTC)
BINARY = "inbox/emails/2026/03/note/attachments/report.pdf"


@pytest.fixture()
def inner(tmp_path) -> LocalBackend:
    return LocalBackend(str(tmp_path / "vault"))


@pytest.fixture()
def catalog(index_config) -> FileCatalog:
    backend = InMemoryIndexBackend(index_config)
    backend.initialize()
    return FileCatalog(backend, index_config.catalog)


@pytest.fixture()
def storage(inner, catalog) -> CatalogingStorage:
    return CatalogingStorage(inner, catalog, "email", lambda: FIXED_NOW)


def test_a_written_binary_lands_in_storage_and_in_the_catalog(storage, catalog, inner):
    storage.write_bytes(BINARY, b"%PDF-1.4 body")

    assert inner.read_file(BINARY) == "%PDF-1.4 body"
    entry = catalog.get(BINARY)
    assert (entry.file_name, entry.extension, entry.source) == ("report.pdf", ".pdf", "email")
    assert entry.size_bytes == len(b"%PDF-1.4 body")
    assert entry.conversion_status == catalog.initial_state
    assert entry.modified_at == FIXED_NOW.isoformat()


def test_the_extension_is_lower_cased(storage, catalog):
    storage.write_bytes("inbox/emails/x/attachments/REPORT.PDF", b"data")
    assert catalog.get("inbox/emails/x/attachments/REPORT.PDF").extension == ".pdf"


def test_a_binary_with_no_extension_is_still_catalogued(storage, catalog):
    storage.write_bytes("inbox/emails/x/attachments/README", b"data")
    assert catalog.get("inbox/emails/x/attachments/README").extension == ""


def test_markdown_is_not_catalogued(storage, catalog, inner):
    storage.write_file("inbox/emails/x/index.md", "# note")

    assert inner.read_file("inbox/emails/x/index.md") == "# note"
    assert catalog.stats()["total"] == 0


def test_rewriting_the_same_bytes_leaves_the_conversion_state_alone(storage, catalog, index_config):
    """Otherwise every cycle resets converted rows and extract loops forever."""
    storage.write_bytes(BINARY, b"same bytes")
    catalog.set_status(BINARY, index_config.catalog.converted_state, "out.md", None)

    storage.write_bytes(BINARY, b"same bytes")

    entry = catalog.get(BINARY)
    assert (entry.conversion_status, entry.output_path) == (index_config.catalog.converted_state, "out.md")
    assert catalog.stats()["total"] == 1


def test_a_changed_binary_goes_back_to_pending(storage, catalog, index_config):
    """New bytes mean the old markdown describes a file that no longer exists."""
    storage.write_bytes(BINARY, b"first")
    catalog.set_status(BINARY, index_config.catalog.converted_state, "out.md", None)

    storage.write_bytes(BINARY, b"second version, longer")

    entry = catalog.get(BINARY)
    assert entry.conversion_status == catalog.initial_state
    assert (entry.output_path, entry.size_bytes) == (None, len(b"second version, longer"))


def test_deleting_a_binary_removes_its_row(storage, catalog, inner):
    storage.write_bytes(BINARY, b"data")
    storage.delete_file(BINARY)

    assert catalog.get(BINARY) is None
    assert inner.file_exists(BINARY) is False


def test_the_reads_pass_straight_through(storage, inner):
    inner.write_file("inbox/emails/x/index.md", "# note")

    assert storage.file_exists("inbox/emails/x/index.md") is True
    assert storage.read_file("inbox/emails/x/index.md") == "# note"
    assert storage.list_files("inbox") == ["inbox/emails/x/index.md"]


def test_without_an_index_section_the_storage_is_handed_back_untouched(inner):
    """No index means no catalog to write to. That is a configuration, not a failure."""
    with catalog_writes(None, inner, "email", lambda: FIXED_NOW) as wrapped:
        assert wrapped is inner


def test_the_context_manager_catalogs_and_then_closes_the_backend(inner, index_payload):
    config = IndexConfig.model_validate(index_payload)

    with catalog_writes(config, inner, "onedrive", lambda: FIXED_NOW) as wrapped:
        wrapped.write_bytes(BINARY, b"data")

    from m365_brain.index.backends import create_index_backend

    backend = create_index_backend(config)
    backend.initialize()
    try:
        entry = FileCatalog(backend, config.catalog).get(BINARY)
    finally:
        backend.close()
    assert entry.source == "onedrive"
