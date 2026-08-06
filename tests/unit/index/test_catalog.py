"""The file catalog lifecycle, against the fake backend.

The interesting assertions are about the conversion vocabulary. It is one
config list, and this layer's whole reason to exist is that an unknown state is
rejected on the way in rather than stored and discovered later by a reader that
does not recognise it.
"""

from __future__ import annotations

import pytest

from m365_brain.config.index import IndexConfig
from m365_brain.index.backends.memory import InMemoryIndexBackend
from m365_brain.index.catalog import FileCatalog

from .conftest import a_catalog_entry, a_catalog_query


@pytest.fixture()
def catalog(index_payload) -> FileCatalog:
    config = IndexConfig.model_validate(index_payload)
    backend = InMemoryIndexBackend(config)
    backend.initialize()
    return FileCatalog(backend, config.catalog)


def test_upsert_by_path_is_idempotent(catalog):
    first = catalog.upsert(a_catalog_entry())
    second = catalog.upsert(a_catalog_entry(size_bytes=2048))

    assert first == second
    assert catalog.get("drive/report.docx").size_bytes == 2048
    assert catalog.stats()["total"] == 1


def test_a_stored_entry_comes_back_by_id(catalog):
    entry_id = catalog.upsert(a_catalog_entry())
    assert catalog.get_by_id(entry_id).original_path == "drive/report.docx"


def test_an_unknown_path_is_none(catalog):
    assert catalog.get("drive/never-seen.docx") is None


def test_upserting_an_unconfigured_state_raises(catalog):
    with pytest.raises(ValueError, match="conversion_states"):
        catalog.upsert(a_catalog_entry(conversion_status="quantum"))


def test_setting_an_unconfigured_state_raises(catalog):
    catalog.upsert(a_catalog_entry())
    with pytest.raises(ValueError, match="conversion_states"):
        catalog.set_status("drive/report.docx", "quantum", output_path=None, error=None)


def test_searching_an_unconfigured_state_raises(catalog):
    """A typo in a filter would otherwise read as "no files", which is a different fact."""
    with pytest.raises(ValueError, match="conversion_states"):
        catalog.search(a_catalog_query(status="quantum"))


def test_success_records_an_output_and_clears_any_error(catalog):
    catalog.upsert(a_catalog_entry())
    catalog.set_status("drive/report.docx", "failed", output_path=None, error="boom")
    catalog.set_status("drive/report.docx", "converted", output_path="out/report.md", error=None)

    entry = catalog.get("drive/report.docx")
    assert (entry.conversion_status, entry.output_path, entry.error) == ("converted", "out/report.md", None)


def test_failure_records_an_error_and_drops_a_stale_output(catalog):
    catalog.upsert(a_catalog_entry())
    catalog.set_status("drive/report.docx", "converted", output_path="out/report.md", error=None)
    catalog.set_status("drive/report.docx", "failed", output_path=None, error="boom")

    entry = catalog.get("drive/report.docx")
    assert (entry.conversion_status, entry.output_path, entry.error) == ("failed", None, "boom")


@pytest.mark.parametrize(
    ("filters", "expected"),
    [
        ({"extension": ".pdf"}, ["drive/manual.pdf"]),
        ({"extractor": "mail"}, ["mail/note.docx"]),
        ({"status": "converted"}, ["drive/manual.pdf"]),
        ({"name_contains": "manual"}, ["drive/manual.pdf"]),
        ({"modified_at": None}, ["drive/manual.pdf", "mail/note.docx", "drive/report.docx"]),
    ],
)
def test_every_filter_narrows(catalog, filters, expected):
    catalog.upsert(a_catalog_entry())
    catalog.upsert(
        a_catalog_entry(
            original_path="drive/manual.pdf",
            file_name="manual.pdf",
            extension=".pdf",
            modified_at="2026-03-01T00:00:00Z",
            conversion_status="converted",
        )
    )
    catalog.upsert(
        a_catalog_entry(
            original_path="mail/note.docx",
            file_name="note.docx",
            extractor="mail",
            modified_at="2026-02-01T00:00:00Z",
        )
    )

    query = a_catalog_query(**{k: v for k, v in filters.items() if k != "modified_at"})
    assert [entry.original_path for entry in catalog.search(query)] == expected


def test_stats_name_every_configured_state_and_sum_to_the_total(catalog, index_config):
    catalog.upsert(a_catalog_entry())
    catalog.upsert(a_catalog_entry(original_path="drive/other.docx", conversion_status="converted"))

    stats = catalog.stats()

    assert set(stats) == {"total", *index_config.catalog.conversion_states}
    assert sum(stats[state] for state in index_config.catalog.conversion_states) == stats["total"] == 2


def test_removal_reports_whether_a_row_existed(catalog):
    catalog.upsert(a_catalog_entry())
    assert catalog.remove("drive/report.docx") is True
    assert catalog.remove("drive/report.docx") is False


def test_the_initial_state_comes_from_config(catalog, index_config):
    assert catalog.initial_state == index_config.catalog.initial_state
