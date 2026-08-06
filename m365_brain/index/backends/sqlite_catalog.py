"""The SQLite file catalog: non-markdown sources and their conversion state.

`catalog_stats` is generated from `index.catalog.conversion_states` rather than
written out. The hand-written version listed five `SUM(CASE WHEN ...)` columns,
which meant the SQL and the config each had their own idea of the vocabulary and
adding a state changed behaviour in one place only.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence

from m365_brain.index.backends.filters import normalised_extension
from m365_brain.model import CatalogEntry, CatalogQuery

COLUMNS = (
    "id, extractor, original_path, file_name, extension, size_bytes, "
    "modified_at, conversion_status, output_path, error_message"
)

LIKE_ESCAPE = "\\"
"""`_` and `%` are LIKE wildcards, and filenames are full of underscores.

Unescaped, a search for `annual_report` also matches `annualXreport`, and a
search for `%` matches every row in the table. Neither is visible until the
catalog has rows in it, which is exactly how both survived this long.
"""


def upsert_catalog_entry(conn: sqlite3.Connection, entry: CatalogEntry) -> int:
    """Insert or update by `original_path`. Returns the row id."""
    conn.execute(
        """INSERT INTO file_catalog
               (extractor, original_path, file_name, extension, size_bytes, modified_at,
                conversion_status, output_path, error_message)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(original_path) DO UPDATE SET
               extractor = excluded.extractor,
               file_name = excluded.file_name,
               extension = excluded.extension,
               size_bytes = excluded.size_bytes,
               modified_at = excluded.modified_at,
               conversion_status = excluded.conversion_status,
               output_path = excluded.output_path,
               error_message = excluded.error_message""",
        (
            entry.extractor,
            entry.original_path,
            entry.file_name,
            entry.extension,
            entry.size_bytes,
            entry.modified_at,
            entry.conversion_status,
            entry.output_path,
            entry.error,
        ),
    )
    row = conn.execute("SELECT id FROM file_catalog WHERE original_path = ?", (entry.original_path,)).fetchone()
    return int(row["id"])


def search_catalog(conn: sqlite3.Connection, query: CatalogQuery) -> list[CatalogEntry]:
    where, params = _where(query)
    rows = conn.execute(
        f"SELECT {COLUMNS} FROM file_catalog {where} ORDER BY modified_at DESC LIMIT ?",
        (*params, query.limit),
    ).fetchall()
    return [_entry(row) for row in rows]


def count_catalog(conn: sqlite3.Connection, query: CatalogQuery) -> int:
    """The same filters, uncapped. `query.limit` is deliberately not applied."""
    where, params = _where(query)
    return int(conn.execute(f"SELECT COUNT(*) AS n FROM file_catalog {where}", tuple(params)).fetchone()["n"])


def _where(query: CatalogQuery) -> tuple[str, list[object]]:
    """One rendering of `CatalogQuery`, shared by the rows and their count.

    Two renderings would be two chances to disagree, and a count computed from
    a different filter than the rows is worse than no count at all.
    """
    clauses: list[str] = []
    params: list[object] = []

    if query.name_contains is not None:
        clauses.append(f"file_name LIKE ? ESCAPE '{LIKE_ESCAPE}'")
        params.append(_contains_pattern(query.name_contains))
    if query.extractor is not None:
        clauses.append("extractor = ?")
        params.append(query.extractor)
    if query.extension is not None:
        clauses.append("extension = ?")
        params.append(normalised_extension(query.extension))
    if query.status is not None:
        clauses.append("conversion_status = ?")
        params.append(query.status)
    if query.modified_after is not None:
        clauses.append("modified_at >= ?")
        params.append(query.modified_after)

    return (f"WHERE {' AND '.join(clauses)}" if clauses else "", params)


def get_catalog_entry(conn: sqlite3.Connection, original_path: str) -> CatalogEntry | None:
    row = conn.execute(f"SELECT {COLUMNS} FROM file_catalog WHERE original_path = ?", (original_path,)).fetchone()
    return _entry(row) if row else None


def get_catalog_entry_by_id(conn: sqlite3.Connection, entry_id: int) -> CatalogEntry | None:
    row = conn.execute(f"SELECT {COLUMNS} FROM file_catalog WHERE id = ?", (entry_id,)).fetchone()
    return _entry(row) if row else None


def set_catalog_status(
    conn: sqlite3.Connection,
    original_path: str,
    state: str,
    output_path: str | None,
    error: str | None,
) -> None:
    """Move a row to a state, replacing both `output_path` and `error`.

    Both are written unconditionally rather than merged: a success that left a
    previous run's error message in place would make the row read as failed.
    """
    conn.execute(
        """UPDATE file_catalog
           SET conversion_status = ?, output_path = ?, error_message = ?
           WHERE original_path = ?""",
        (state, output_path, error, original_path),
    )


def remove_catalog_entry(conn: sqlite3.Connection, original_path: str) -> bool:
    cursor = conn.execute("DELETE FROM file_catalog WHERE original_path = ?", (original_path,))
    return cursor.rowcount > 0


def catalog_stats(conn: sqlite3.Connection, conversion_states: Sequence[str]) -> dict[str, int]:
    """`total` plus one count per configured state, zeros included.

    A state with no rows still gets a key: a caller rendering a summary should
    not have to guess whether a missing key means zero or means the state was
    never configured.
    """
    counts = {
        row["conversion_status"]: row["n"]
        for row in conn.execute(
            "SELECT conversion_status, COUNT(*) AS n FROM file_catalog GROUP BY conversion_status"
        ).fetchall()
    }
    total = int(conn.execute("SELECT COUNT(*) AS n FROM file_catalog").fetchone()["n"])
    return {"total": total, **{state: int(counts.get(state, 0)) for state in conversion_states}}


def _contains_pattern(text: str) -> str:
    """A literal substring as a LIKE pattern, wildcards neutralised."""
    escaped = text.replace(LIKE_ESCAPE, LIKE_ESCAPE * 2).replace("%", f"{LIKE_ESCAPE}%").replace("_", f"{LIKE_ESCAPE}_")
    return f"%{escaped}%"


def _entry(row: sqlite3.Row) -> CatalogEntry:
    return CatalogEntry(
        entry_id=row["id"],
        original_path=row["original_path"],
        file_name=row["file_name"],
        extension=row["extension"],
        extractor=row["extractor"],
        size_bytes=row["size_bytes"],
        modified_at=row["modified_at"],
        conversion_status=row["conversion_status"],
        output_path=row["output_path"],
        error=row["error_message"],
    )
