"""SQL-level guarantees of the read path.

`test_base.py` proves the behaviour is shared. What is left here is what only
SQLite can be asked: that ranking really is bm25 with the configured weights,
that the snippet markers arrive from config, and that a numeric metadata filter
is cast before it is compared -- without the cast every string sorts above every
number in SQLite and `priority >= 5` matches everything.
"""

from __future__ import annotations

import pytest

from m365_brain.index.backends.base import MetadataFilter
from tests.unit.index.conftest import a_text_query, an_entity, an_observation, make_backend


def indexed(backend, entities) -> None:
    backend.upsert_entities(entities)
    backend.rebuild_text_index()


def test_find_prefers_an_exact_title_over_a_partial_one(sqlite_backend):
    sqlite_backend.upsert_entities(
        [
            an_entity(key="corpus/a.md", permalink="a", title="Bob Jones Senior"),
            an_entity(key="corpus/b.md", permalink="b", title="Bob"),
        ]
    )
    found = sqlite_backend.find_entity("Bob", by_permalink=False)
    assert found is not None
    assert found.title == "Bob"


def test_find_prefers_an_alias_over_a_partial_title(sqlite_backend):
    sqlite_backend.upsert_entities(
        [
            an_entity(key="corpus/a.md", permalink="a", title="Alicia Partial"),
            an_entity(key="corpus/b.md", permalink="b", title="Someone Else", aliases=["Alicia"]),
        ]
    )
    found = sqlite_backend.find_entity("Alicia", by_permalink=False)
    assert found is not None
    assert found.title == "Someone Else"


def test_an_entity_without_aliases_still_falls_through(sqlite_backend):
    """`json_each` over a NULL column must not break the alias stage."""
    sqlite_backend.upsert_entities([an_entity(key="corpus/b.md", permalink="b", title="Bob Jones")])
    found = sqlite_backend.find_entity("Jones", by_permalink=False)
    assert found is not None
    assert found.title == "Bob Jones"


def test_ranking_uses_the_configured_bm25_weights(index_payload):
    """Title weight 10 vs content weight 1: a title hit must outrank a body hit."""
    backend = make_backend(index_payload, "sqlite")
    indexed(
        backend,
        [
            an_entity(key="corpus/body.md", permalink="body", title="Body", content="Body\ntelescope"),
            an_entity(key="corpus/title.md", permalink="title", title="telescope", content="telescope\n"),
        ],
    )
    hits = backend.text_search(a_text_query(fts="telescope")).hits
    assert [hit.entity.key for hit in hits][0] == "corpus/title.md"


def test_scores_are_positive_so_higher_is_better(sqlite_backend):
    indexed(sqlite_backend, [an_entity(content="Note\ntelescope")])
    hit = sqlite_backend.text_search(a_text_query(fts="telescope")).hits[0]
    assert hit.score > 0


def test_snippet_markers_come_from_config(index_payload):
    index_payload["search"]["snippet"]["start_marker"] = "<<"
    index_payload["search"]["snippet"]["end_marker"] = ">>"
    backend = make_backend(index_payload, "sqlite")
    indexed(backend, [an_entity(content="Note\na telescope in the garden")])
    hit = backend.text_search(a_text_query(fts="telescope")).hits[0]
    assert "<<telescope>>" in hit.snippet


def test_snippet_column_is_validated(index_payload):
    index_payload["search"]["snippet"]["column"] = "permalink"
    backend = make_backend(index_payload, "sqlite")
    indexed(backend, [an_entity(content="Note\ntelescope")])
    with pytest.raises(ValueError, match="permalink"):
        backend.text_search(a_text_query(fts="telescope"))


def test_fts_matching_is_token_based(sqlite_backend):
    """Unlike the fake's substring scan: a bare prefix does not match."""
    indexed(sqlite_backend, [an_entity(content="Note\ntelescope")])
    assert sqlite_backend.text_search(a_text_query(fts="telesc")).hits == []
    assert len(sqlite_backend.text_search(a_text_query(fts="telesc*")).hits) == 1


def test_numeric_metadata_filter_is_cast_before_comparison(sqlite_backend):
    indexed(
        sqlite_backend,
        [
            an_entity(key="corpus/a.md", permalink="a", metadata={"priority": "3"}),
            an_entity(key="corpus/b.md", permalink="b", metadata={"priority": "10"}),
        ],
    )
    matched = sqlite_backend.text_search(
        a_text_query(metadata=(MetadataFilter(key="priority", op="gt", values=(5.0,)),))
    )
    assert [hit.entity.key for hit in matched.hits] == ["corpus/b.md"]


def test_nested_metadata_keys_use_a_json_path(sqlite_backend):
    indexed(sqlite_backend, [an_entity(metadata={"origin": {"system": "s3"}})])
    matched = sqlite_backend.text_search(
        a_text_query(metadata=(MetadataFilter(key="origin.system", op="eq", values=("s3",)),))
    )
    assert len(matched.hits) == 1


def test_metadata_in_and_between(sqlite_backend):
    indexed(
        sqlite_backend,
        [
            an_entity(key="corpus/a.md", permalink="a", metadata={"status": "open", "score": "2"}),
            an_entity(key="corpus/b.md", permalink="b", metadata={"status": "done", "score": "7"}),
        ],
    )
    in_filter = MetadataFilter(key="status", op="in", values=("open", "blocked"))
    assert [h.entity.key for h in sqlite_backend.text_search(a_text_query(metadata=(in_filter,))).hits] == [
        "corpus/a.md"
    ]
    between = MetadataFilter(key="score", op="between", values=(5.0, 9.0))
    assert [h.entity.key for h in sqlite_backend.text_search(a_text_query(metadata=(between,))).hits] == ["corpus/b.md"]


def test_filters_apply_to_a_full_text_query_too(sqlite_backend):
    indexed(
        sqlite_backend,
        [
            an_entity(key="corpus/a.md", permalink="a", entity_type="person", content="A\ntelescope"),
            an_entity(key="corpus/b.md", permalink="b", entity_type="note", content="B\ntelescope"),
        ],
    )
    matched = sqlite_backend.text_search(a_text_query(fts="telescope", entity_type="person"))
    assert [hit.entity.key for hit in matched.hits] == ["corpus/a.md"]
    assert matched.total == 1


def test_filter_only_listing_is_newest_first(sqlite_backend):
    indexed(
        sqlite_backend,
        [
            an_entity(key="corpus/a.md", permalink="a", updated_at="2026-01-01T00:00:00Z"),
            an_entity(key="corpus/b.md", permalink="b", updated_at="2026-05-01T00:00:00Z"),
        ],
    )
    hits = sqlite_backend.text_search(a_text_query()).hits
    assert [hit.entity.key for hit in hits] == ["corpus/b.md", "corpus/a.md"]


def test_observation_tags_survive_the_json_round_trip(sqlite_backend):
    tagged = an_observation("Note", "something")
    sqlite_backend.upsert_entities([an_entity(observations=[tagged])])
    entity_id = sqlite_backend.indexed_files()["corpus/note.md"].entity_id
    assert sqlite_backend.get_observations(entity_id)[0].tags == []


def test_iter_indexed_text_reads_the_text_index_not_the_entities(sqlite_backend):
    sqlite_backend.upsert_entities([an_entity()])
    assert list(sqlite_backend.iter_indexed_text()) == []
    sqlite_backend.rebuild_text_index()
    assert len(list(sqlite_backend.iter_indexed_text())) == 1
