"""SQL-level guarantees of the file catalog.

The lifecycle is proved for both backends in `test_base.py`. What is asserted
here is the SQL that only this backend runs: the upsert really is one statement
keyed on `original_path`, and `catalog_stats` is generated from the configured
states rather than from a hand-written list of `SUM(CASE WHEN ...)` columns.
"""

from __future__ import annotations

from tests.unit.index.conftest import a_catalog_entry, a_catalog_query, make_backend


def test_upsert_keeps_one_row_per_path(sqlite_backend):
    sqlite_backend.upsert_catalog_entry(a_catalog_entry(size_bytes=1))
    sqlite_backend.upsert_catalog_entry(a_catalog_entry(size_bytes=2))
    with sqlite_backend.connect(readonly=True) as conn:
        assert conn.execute("SELECT COUNT(*) FROM file_catalog").fetchone()[0] == 1


def test_upsert_returns_a_stable_id(sqlite_backend):
    first = sqlite_backend.upsert_catalog_entry(a_catalog_entry(size_bytes=1))
    second = sqlite_backend.upsert_catalog_entry(a_catalog_entry(size_bytes=2))
    assert first == second


def test_set_status_clears_a_previous_error(sqlite_backend):
    sqlite_backend.upsert_catalog_entry(a_catalog_entry())
    sqlite_backend.set_catalog_status("drive/report.docx", "failed", None, "broke")
    sqlite_backend.set_catalog_status("drive/report.docx", "converted", "out.md", None)
    with sqlite_backend.connect(readonly=True) as conn:
        row = conn.execute("SELECT * FROM file_catalog").fetchone()
    assert row["error_message"] is None
    assert row["output_path"] == "out.md"


def test_stats_keys_follow_the_configured_states(index_payload):
    index_payload["catalog"]["conversion_states"] = ["pending", "done"]
    index_payload["catalog"]["initial_state"] = "pending"
    backend = make_backend(index_payload, "sqlite")
    backend.upsert_catalog_entry(a_catalog_entry())
    stats = backend.catalog_stats()
    assert set(stats) == {"total", "pending", "done"}
    assert stats == {"total": 1, "pending": 1, "done": 0}


def test_stats_ignores_a_row_in_an_unconfigured_state(index_payload):
    """The total still counts it -- the summary must not silently lose rows."""
    index_payload["catalog"]["conversion_states"] = ["pending"]
    index_payload["catalog"]["initial_state"] = "pending"
    backend = make_backend(index_payload, "sqlite")
    backend.upsert_catalog_entry(a_catalog_entry(conversion_status="unexpected"))
    stats = backend.catalog_stats()
    assert stats["total"] == 1
    assert stats["pending"] == 0


def test_search_orders_by_modification_time(sqlite_backend):
    sqlite_backend.upsert_catalog_entry(a_catalog_entry(modified_at="2026-01-01T00:00:00Z"))
    sqlite_backend.upsert_catalog_entry(
        a_catalog_entry(
            original_path="drive/newer.docx",
            file_name="newer.docx",
            modified_at="2026-06-01T00:00:00Z",
        )
    )
    assert [e.file_name for e in sqlite_backend.search_catalog(a_catalog_query())] == [
        "newer.docx",
        "report.docx",
    ]


def test_search_combines_filters(sqlite_backend):
    sqlite_backend.upsert_catalog_entry(a_catalog_entry())
    sqlite_backend.upsert_catalog_entry(
        a_catalog_entry(
            original_path="mail/report.pdf",
            file_name="report.pdf",
            extension=".pdf",
            source="mail",
        )
    )
    matched = sqlite_backend.search_catalog(a_catalog_query(name_contains="report", source="mail"))
    assert [e.file_name for e in matched] == ["report.pdf"]


def test_removing_a_missing_row_reports_false(sqlite_backend):
    assert sqlite_backend.remove_catalog_entry("nothing/here.docx") is False
