"""SQL-level guarantees of the write path.

Three of these are invisible above the adapter and each was a real defect
somewhere: the cascade depends on a pragma that SQLite defaults off, the resolve
count means "resolved" rather than "attempted", and the text index has to carry
the document body or prose-only files are unsearchable.
"""

from __future__ import annotations

import json

from tests.unit.index.conftest import a_relation, an_entity, an_observation


def rows(backend, sql: str, params: tuple = ()) -> list:
    with backend.connect(readonly=True) as conn:
        return conn.execute(sql, params).fetchall()


def test_cascade_delete_removes_children(sqlite_backend):
    sqlite_backend.upsert_entities(
        [
            an_entity(
                observations=[an_observation("Role", "Engineer")],
                relations=[a_relation("knows", "Other")],
            )
        ]
    )
    sqlite_backend.delete_entities(["corpus/note.md"])
    assert rows(sqlite_backend, "SELECT * FROM observation") == []
    assert rows(sqlite_backend, "SELECT * FROM relation") == []


def test_reupsert_replaces_children_rather_than_appending(sqlite_backend):
    sqlite_backend.upsert_entities([an_entity(observations=[an_observation("Role", "Engineer")])])
    sqlite_backend.upsert_entities([an_entity(observations=[an_observation("Role", "Analyst")])])
    stored = rows(sqlite_backend, "SELECT content FROM observation")
    assert [r["content"] for r in stored] == ["Analyst"]


def test_duplicate_edges_are_collapsed_by_the_unique_constraint(sqlite_backend):
    duplicate = [a_relation("knows", "Other"), a_relation("knows", "Other")]
    sqlite_backend.upsert_entities([an_entity(relations=duplicate)])
    assert len(rows(sqlite_backend, "SELECT * FROM relation")) == 1


def test_tags_aliases_and_metadata_are_stored_as_json(sqlite_backend):
    sqlite_backend.upsert_entities([an_entity(tags=["a", "b"], aliases=["Al"], metadata={"priority": "3"})])
    row = rows(sqlite_backend, "SELECT tags, aliases, metadata FROM entity")[0]
    assert json.loads(row["tags"]) == ["a", "b"]
    assert json.loads(row["aliases"]) == ["Al"]
    assert json.loads(row["metadata"]) == {"priority": "3"}


def test_empty_collections_are_stored_as_null(sqlite_backend):
    sqlite_backend.upsert_entities([an_entity()])
    row = rows(sqlite_backend, "SELECT tags, aliases, metadata FROM entity")[0]
    assert row["tags"] is None
    assert row["aliases"] is None
    assert row["metadata"] is None


def test_resolve_counts_resolutions_not_attempts(sqlite_backend):
    """The UPDATE touches every unresolved row, including the ones it cannot
    resolve. Its rowcount would report two here; only one edge found a target."""
    sqlite_backend.upsert_entities(
        [
            an_entity(
                key="corpus/a.md",
                permalink="a",
                title="A",
                relations=[a_relation("knows", "B"), a_relation("knows", "Ghost")],
            ),
            an_entity(key="corpus/b.md", permalink="b", title="B"),
        ]
    )
    assert sqlite_backend.resolve_relations() == 1


def test_resolve_is_idempotent(sqlite_backend):
    sqlite_backend.upsert_entities(
        [
            an_entity(key="corpus/a.md", permalink="a", title="A", relations=[a_relation("knows", "B")]),
            an_entity(key="corpus/b.md", permalink="b", title="B"),
        ]
    )
    assert sqlite_backend.resolve_relations() == 1
    assert sqlite_backend.resolve_relations() == 0


def test_resolve_matches_a_permalink(sqlite_backend):
    sqlite_backend.upsert_entities(
        [
            an_entity(key="corpus/a.md", permalink="a", title="A", relations=[a_relation("knows", "b")]),
            an_entity(key="corpus/b.md", permalink="b", title="B"),
        ]
    )
    assert sqlite_backend.resolve_relations() == 1


def test_text_index_carries_the_body_of_a_prose_only_file(sqlite_backend):
    """A file with no observation lines still has to be findable."""
    sqlite_backend.upsert_entities([an_entity(content="Note\nan entirely prose document")])
    sqlite_backend.rebuild_text_index()
    row = rows(sqlite_backend, "SELECT content FROM search_index")[0]
    assert "entirely prose document" in row["content"]


def test_text_index_carries_observations_and_body_together(sqlite_backend):
    sqlite_backend.upsert_entities(
        [an_entity(observations=[an_observation("Role", "Engineer")], content="Note\nprose")]
    )
    sqlite_backend.rebuild_text_index()
    content = rows(sqlite_backend, "SELECT content FROM search_index")[0]["content"]
    assert "Role: Engineer" in content
    assert "prose" in content


def test_rebuild_replaces_rather_than_appends(sqlite_backend):
    sqlite_backend.upsert_entities([an_entity()])
    sqlite_backend.rebuild_text_index()
    sqlite_backend.rebuild_text_index()
    assert len(rows(sqlite_backend, "SELECT * FROM search_index")) == 1


def test_delete_of_nothing_is_not_a_query(sqlite_backend):
    assert sqlite_backend.delete_entities([]) == 0
