"""The SQLite read path: lookup, edges, text search, hydration.

`MetadataFilter` is rendered to `json_extract` here and nowhere else. That is the
point of the structured filter: a caller that wanted `priority>=3` would
otherwise have to build SQL, and every consumer that builds SQL is a consumer
that cannot move to another store.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Sequence
from typing import Any

from m365_brain.config.index import SearchConfig
from m365_brain.index.backends.base import MetadataFilter, TextQuery
from m365_brain.index.backends.filters import SQL_COMPARISON
from m365_brain.index.backends.sqlite_schema import search_column_index
from m365_brain.model import EntityRef, Observation, RelationEdge, SearchHit, SearchPage

ENTITY_COLUMNS = "e.id, e.entity_key, e.title, e.type, e.permalink, e.file_path, e.updated_at"


def find_entity(conn: sqlite3.Connection, identifier: str, by_permalink: bool) -> EntityRef | None:
    """Exact permalink, or title -> alias -> partial title.

    The fallbacks are ordered by confidence, and the partial match is last
    because it is the one that can be wrong.
    """
    if by_permalink:
        row = conn.execute(f"SELECT {ENTITY_COLUMNS} FROM entity e WHERE e.permalink = ?", (identifier,)).fetchone()
        return _ref(row) if row else None

    for sql, params in (
        (f"SELECT {ENTITY_COLUMNS} FROM entity e WHERE e.title = ? COLLATE NOCASE", (identifier,)),
        (
            f"""SELECT {ENTITY_COLUMNS} FROM entity e
                WHERE e.aliases IS NOT NULL
                  AND EXISTS (SELECT 1 FROM json_each(e.aliases) j WHERE j.value = ? COLLATE NOCASE)
                LIMIT 1""",
            (identifier,),
        ),
        (
            f"SELECT {ENTITY_COLUMNS} FROM entity e WHERE e.title LIKE ? COLLATE NOCASE LIMIT 1",
            (f"%{identifier}%",),
        ),
    ):
        row = conn.execute(sql, params).fetchone()
        if row:
            return _ref(row)
    return None


def get_observations(conn: sqlite3.Connection, entity_id: int) -> list[Observation]:
    rows = conn.execute(
        "SELECT category, content, tags, context FROM observation WHERE entity_id = ? ORDER BY id",
        (entity_id,),
    ).fetchall()
    return [
        Observation(
            category=r["category"],
            content=r["content"],
            tags=json.loads(r["tags"]) if r["tags"] else [],
            context=r["context"],
        )
        for r in rows
    ]


def outgoing_relations(conn: sqlite3.Connection, entity_ids: Sequence[int]) -> list[RelationEdge]:
    return _edges(conn, "from_entity_id", entity_ids)


def incoming_relations(conn: sqlite3.Connection, entity_ids: Sequence[int]) -> list[RelationEdge]:
    return _edges(conn, "to_entity_id", entity_ids)


def _edges(conn: sqlite3.Connection, column: str, entity_ids: Sequence[int]) -> list[RelationEdge]:
    if not entity_ids:
        return []
    placeholders = ",".join("?" * len(entity_ids))
    rows = conn.execute(
        f"""SELECT from_entity_id, to_entity_id, to_name, relation_type, context
            FROM relation WHERE {column} IN ({placeholders}) ORDER BY id""",
        tuple(entity_ids),
    ).fetchall()
    return [
        RelationEdge(
            from_entity_id=r["from_entity_id"],
            to_entity_id=r["to_entity_id"],
            to_name=r["to_name"],
            relation_type=r["relation_type"],
            context=r["context"],
        )
        for r in rows
    ]


def text_search(conn: sqlite3.Connection, query: TextQuery, config: SearchConfig) -> SearchPage:
    clauses, params = _filter_clauses(query)
    offset = (query.page - 1) * query.page_size

    if query.fts is None:
        source = "FROM entity e"
        where = _where(clauses)
        total = _count(conn, f"SELECT COUNT(*) AS n {source} {where}", params)
        rows = conn.execute(
            f"SELECT {ENTITY_COLUMNS}, 0.0 AS rank, NULL AS snip {source} {where} "
            "ORDER BY e.updated_at DESC LIMIT ? OFFSET ?",
            (*params, query.page_size, offset),
        ).fetchall()
        return _page(rows, total, query)

    source = "FROM search_index JOIN entity e ON e.id = search_index.rowid"
    where = _where(["search_index MATCH ?", *clauses])
    match_params = (query.fts, *params)
    total = _count(conn, f"SELECT COUNT(*) AS n {source} {where}", match_params)

    weights = config.bm25_weights
    snippet = config.snippet
    rank = f"bm25(search_index, {float(weights.title)}, {float(weights.content)}, {float(weights.tags)})"
    snip = f"snippet(search_index, {search_column_index(snippet.column)}, ?, ?, ?, {int(snippet.max_tokens)})"
    rows = conn.execute(
        f"SELECT {ENTITY_COLUMNS}, {rank} AS rank, {snip} AS snip {source} {where} ORDER BY rank LIMIT ? OFFSET ?",
        (
            snippet.start_marker,
            snippet.end_marker,
            snippet.ellipsis,
            *match_params,
            query.page_size,
            offset,
        ),
    ).fetchall()
    return _page(rows, total, query)


def recent_entities(conn: sqlite3.Connection, updated_since: str, limit: int) -> list[EntityRef]:
    rows = conn.execute(
        f"SELECT {ENTITY_COLUMNS} FROM entity e WHERE e.updated_at >= ? ORDER BY e.updated_at DESC LIMIT ?",
        (updated_since, limit),
    ).fetchall()
    return [_ref(r) for r in rows]


def hydrate(conn: sqlite3.Connection, entity_ids: Sequence[int]) -> dict[int, EntityRef]:
    if not entity_ids:
        return {}
    placeholders = ",".join("?" * len(entity_ids))
    rows = conn.execute(
        f"SELECT {ENTITY_COLUMNS} FROM entity e WHERE e.id IN ({placeholders})", tuple(entity_ids)
    ).fetchall()
    return {r["id"]: _ref(r) for r in rows}


def iter_indexed_text(conn: sqlite3.Connection) -> Iterator[tuple[int, str]]:
    rows = conn.execute("SELECT rowid, title, content FROM search_index").fetchall()
    return iter([(r["rowid"], f"{r['title']}\n\n{r['content']}") for r in rows])


# -- internals ------------------------------------------------------------


def _page(rows: list[sqlite3.Row], total: int, query: TextQuery) -> SearchPage:
    hits = [SearchHit(entity=_ref(r), score=abs(r["rank"]), snippet=r["snip"]) for r in rows]
    return SearchPage(hits=hits, total=total, page=query.page, page_size=query.page_size)


def _count(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...]) -> int:
    return int(conn.execute(sql, params).fetchone()["n"])


def _where(clauses: list[str]) -> str:
    return f"WHERE {' AND '.join(clauses)}" if clauses else ""


def _filter_clauses(query: TextQuery) -> tuple[list[str], tuple[Any, ...]]:
    clauses: list[str] = []
    params: list[Any] = []
    if query.entity_type is not None:
        clauses.append("e.type = ?")
        params.append(query.entity_type)
    if query.tag is not None:
        clauses.append("(e.tags IS NOT NULL AND EXISTS (SELECT 1 FROM json_each(e.tags) j WHERE j.value = ?))")
        params.append(query.tag)
    for metadata_filter in query.metadata:
        clause, values = _metadata_clause(metadata_filter)
        clauses.append(clause)
        params.extend(values)
    return clauses, tuple(params)


def _metadata_clause(metadata_filter: MetadataFilter) -> tuple[str, list[Any]]:
    """`json_extract` plus a comparison, cast to REAL when the filter is numeric.

    Frontmatter scalars are stored as text, so an uncast `>= 3` compares a string
    against a number -- and in SQLite's type ordering every string sorts above
    every number, which would make the filter always true.
    """
    numeric = all(isinstance(v, float) for v in metadata_filter.values)
    extract = "json_extract(e.metadata, ?)"
    expression = f"CAST({extract} AS REAL)" if numeric else extract
    path = f"$.{metadata_filter.key}"
    op = metadata_filter.op

    if op == "in":
        placeholders = ",".join("?" * len(metadata_filter.values))
        return f"{expression} IN ({placeholders})", [path, *metadata_filter.values]
    if op == "between":
        return f"{expression} BETWEEN ? AND ?", [path, *metadata_filter.values]
    return f"{expression} {SQL_COMPARISON[op]} ?", [path, metadata_filter.values[0]]


def _ref(row: sqlite3.Row) -> EntityRef:
    return EntityRef(
        entity_id=row["id"],
        key=row["entity_key"],
        title=row["title"],
        entity_type=row["type"],
        permalink=row["permalink"],
        file_path=row["file_path"],
        updated_at=row["updated_at"],
    )
