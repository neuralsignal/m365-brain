"""The interchangeability proof for `IndexBackend`.

Every assertion here runs against every implementation. Nothing in this file may
name a backend, and nothing may assume FTS5 semantics or a particular result
order -- those are the two places where a real store and a fake legitimately
differ. If an assertion has to change when a new backend joins the fixture, the
protocol leaked that backend's shape and the fix belongs in `base.py`.
"""

from __future__ import annotations

from m365_brain.index.backends.base import IndexBackend, MetadataFilter
from tests.unit.index.conftest import (
    a_catalog_entry,
    a_catalog_query,
    a_relation,
    a_text_query,
    an_entity,
    an_observation,
)


def _keys(page) -> set[str]:
    return {hit.entity.key for hit in page.hits}


def test_is_protocol_instance(backend):
    assert isinstance(backend, IndexBackend)


def test_initialize_is_idempotent(backend):
    backend.initialize()
    backend.initialize()
    assert backend.indexed_files() == {}


def test_upsert_then_find_by_permalink(backend):
    backend.upsert_entities([an_entity(key="corpus/a.md", permalink="alpha", title="Alpha")])
    found = backend.find_entity("alpha", by_permalink=True)
    assert found is not None
    assert found.key == "corpus/a.md"
    assert found.title == "Alpha"


def test_find_by_title_is_case_insensitive(backend):
    backend.upsert_entities([an_entity(key="corpus/a.md", permalink="a", title="Alice Smith")])
    found = backend.find_entity("alice smith", by_permalink=False)
    assert found is not None
    assert found.title == "Alice Smith"


def test_find_by_alias(backend):
    backend.upsert_entities([an_entity(key="corpus/a.md", permalink="a", title="Alice Smith", aliases=["Alice", "AS"])])
    for identifier in ("Alice", "AS", "alice"):
        found = backend.find_entity(identifier, by_permalink=False)
        assert found is not None, identifier
        assert found.title == "Alice Smith"


def test_find_falls_back_to_a_partial_title(backend):
    backend.upsert_entities([an_entity(key="corpus/b.md", permalink="b", title="Bob Jones")])
    found = backend.find_entity("Bob", by_permalink=False)
    assert found is not None
    assert found.title == "Bob Jones"


def test_find_missing_returns_none(backend):
    assert backend.find_entity("nobody", by_permalink=False) is None
    assert backend.find_entity("nobody", by_permalink=True) is None


def test_indexed_files_reports_checksums(backend):
    backend.upsert_entities([an_entity(key="corpus/a.md", permalink="a", checksum="sum-1")])
    indexed = backend.indexed_files()
    assert set(indexed) == {"corpus/a.md"}
    assert indexed["corpus/a.md"].checksum == "sum-1"


def test_upsert_replaces_rather_than_duplicates(backend):
    backend.upsert_entities([an_entity(key="corpus/a.md", permalink="a", checksum="sum-1")])
    backend.upsert_entities([an_entity(key="corpus/a.md", permalink="a", checksum="sum-2")])
    indexed = backend.indexed_files()
    assert len(indexed) == 1
    assert indexed["corpus/a.md"].checksum == "sum-2"


def test_permalink_owners_maps_to_entity_keys(backend):
    backend.upsert_entities(
        [
            an_entity(key="corpus/a.md", permalink="alpha"),
            an_entity(key="corpus/b.md", permalink="beta"),
        ]
    )
    assert backend.permalink_owners() == {"alpha": "corpus/a.md", "beta": "corpus/b.md"}


def test_observations_round_trip(backend):
    backend.upsert_entities(
        [
            an_entity(
                key="corpus/a.md",
                permalink="a",
                observations=[an_observation("Role", "Engineer"), an_observation("City", "Zurich")],
            )
        ]
    )
    entity_id = backend.indexed_files()["corpus/a.md"].entity_id
    stored = backend.get_observations(entity_id)
    assert {(o.category, o.content) for o in stored} == {("Role", "Engineer"), ("City", "Zurich")}


def test_delete_removes_observations_and_relations(backend):
    backend.upsert_entities(
        [
            an_entity(
                key="corpus/a.md",
                permalink="a",
                observations=[an_observation("Role", "Engineer")],
                relations=[a_relation("links_to", "Beta")],
            )
        ]
    )
    entity_id = backend.indexed_files()["corpus/a.md"].entity_id
    assert backend.delete_entities(["corpus/a.md"]) == 1
    assert backend.get_observations(entity_id) == []
    assert backend.outgoing_relations([entity_id]) == []
    assert backend.indexed_files() == {}


def test_delete_of_an_unknown_key_is_not_an_error(backend):
    assert backend.delete_entities(["corpus/never.md"]) == 0


def test_resolve_relations_links_forward_reference(backend):
    backend.upsert_entities(
        [an_entity(key="corpus/a.md", permalink="a", title="A", relations=[a_relation("knows", "B")])]
    )
    assert backend.resolve_relations() == 0  # target does not exist yet

    backend.upsert_entities([an_entity(key="corpus/b.md", permalink="b", title="B")])
    assert backend.resolve_relations() == 1

    ids = backend.indexed_files()
    edges = backend.outgoing_relations([ids["corpus/a.md"].entity_id])
    assert [e.to_entity_id for e in edges] == [ids["corpus/b.md"].entity_id]


def test_resolve_relations_matches_an_alias(backend):
    backend.upsert_entities(
        [
            an_entity(key="corpus/a.md", permalink="a", title="A", relations=[a_relation("knows", "Bee")]),
            an_entity(key="corpus/b.md", permalink="b", title="B", aliases=["Bee"]),
        ]
    )
    assert backend.resolve_relations() == 1


def test_unresolved_relation_keeps_to_name(backend):
    backend.upsert_entities([an_entity(key="corpus/a.md", permalink="a", relations=[a_relation("knows", "Ghost")])])
    backend.resolve_relations()
    entity_id = backend.indexed_files()["corpus/a.md"].entity_id
    edge = backend.outgoing_relations([entity_id])[0]
    assert edge.to_entity_id is None
    assert edge.to_name == "Ghost"


def test_edges_round_trip_both_directions(backend):
    backend.upsert_entities(
        [
            an_entity(key="corpus/a.md", permalink="a", title="A", relations=[a_relation("knows", "B")]),
            an_entity(key="corpus/b.md", permalink="b", title="B"),
        ]
    )
    backend.resolve_relations()
    ids = backend.indexed_files()
    source, target = ids["corpus/a.md"].entity_id, ids["corpus/b.md"].entity_id

    assert [e.relation_type for e in backend.outgoing_relations([source])] == ["knows"]
    assert [e.from_entity_id for e in backend.incoming_relations([target])] == [source]
    assert backend.outgoing_relations([target]) == []
    assert backend.incoming_relations([source]) == []


def test_edges_for_an_empty_frontier(backend):
    assert backend.outgoing_relations([]) == []
    assert backend.incoming_relations([]) == []


def test_deleting_a_target_clears_the_edge_but_keeps_it(backend):
    backend.upsert_entities(
        [
            an_entity(key="corpus/a.md", permalink="a", title="A", relations=[a_relation("knows", "B")]),
            an_entity(key="corpus/b.md", permalink="b", title="B"),
        ]
    )
    backend.resolve_relations()
    ids = backend.indexed_files()
    backend.delete_entities(["corpus/b.md"])
    edge = backend.outgoing_relations([ids["corpus/a.md"].entity_id])[0]
    assert edge.to_entity_id is None
    assert edge.to_name == "B"


def test_text_search_reflects_the_last_rebuild(backend):
    backend.upsert_entities([an_entity(key="corpus/a.md", permalink="a", content="A\ntelescope")])
    assert _keys(backend.text_search(a_text_query(fts="telescope"))) == set()

    backend.rebuild_text_index()
    assert _keys(backend.text_search(a_text_query(fts="telescope"))) == {"corpus/a.md"}


def test_text_search_finds_observation_content(backend):
    backend.upsert_entities(
        [an_entity(key="corpus/a.md", permalink="a", observations=[an_observation("Role", "astronomer")])]
    )
    backend.rebuild_text_index()
    assert _keys(backend.text_search(a_text_query(fts="astronomer"))) == {"corpus/a.md"}


def test_text_search_filters_by_type_and_tag(backend):
    backend.upsert_entities(
        [
            an_entity(key="corpus/a.md", permalink="a", entity_type="person", tags=["team"]),
            an_entity(key="corpus/b.md", permalink="b", entity_type="note", tags=["team"]),
            an_entity(key="corpus/c.md", permalink="c", entity_type="person", tags=["other"]),
        ]
    )
    backend.rebuild_text_index()
    assert _keys(backend.text_search(a_text_query(entity_type="person"))) == {
        "corpus/a.md",
        "corpus/c.md",
    }
    assert _keys(backend.text_search(a_text_query(tag="team"))) == {"corpus/a.md", "corpus/b.md"}
    assert _keys(backend.text_search(a_text_query(entity_type="person", tag="team"))) == {"corpus/a.md"}


def test_text_search_filters_on_metadata(backend):
    backend.upsert_entities(
        [
            an_entity(key="corpus/a.md", permalink="a", metadata={"priority": "3"}),
            an_entity(key="corpus/b.md", permalink="b", metadata={"priority": "10"}),
            an_entity(key="corpus/c.md", permalink="c", metadata={"status": "open"}),
        ]
    )
    backend.rebuild_text_index()
    numeric = MetadataFilter(key="priority", op="gte", values=(5.0,))
    assert _keys(backend.text_search(a_text_query(metadata=(numeric,)))) == {"corpus/b.md"}

    textual = MetadataFilter(key="status", op="eq", values=("open",))
    assert _keys(backend.text_search(a_text_query(metadata=(textual,)))) == {"corpus/c.md"}


def test_text_search_paginates(backend):
    backend.upsert_entities([an_entity(key=f"corpus/{n}.md", permalink=f"p{n}") for n in range(5)])
    backend.rebuild_text_index()
    first = backend.text_search(a_text_query(page=1, page_size=2))
    second = backend.text_search(a_text_query(page=2, page_size=2))
    assert first.total == 5
    assert len(first.hits) == 2
    assert len(second.hits) == 2
    assert _keys(first).isdisjoint(_keys(second))


def test_text_search_excludes_deleted_entities(backend):
    backend.upsert_entities([an_entity(key="corpus/a.md", permalink="a", content="A\ntelescope")])
    backend.rebuild_text_index()
    backend.delete_entities(["corpus/a.md"])
    assert backend.text_search(a_text_query(fts="telescope")).hits == []


def test_recent_entities_are_newest_first(backend):
    backend.upsert_entities(
        [
            an_entity(key="corpus/old.md", permalink="old", updated_at="2026-01-01T00:00:00Z"),
            an_entity(key="corpus/new.md", permalink="new", updated_at="2026-03-01T00:00:00Z"),
        ]
    )
    recent = backend.recent_entities("2026-02-01T00:00:00Z", None, 10)
    assert [ref.key for ref in recent] == ["corpus/new.md"]


def test_recent_entities_honours_the_limit(backend):
    backend.upsert_entities(
        [
            an_entity(key="corpus/a.md", permalink="a", updated_at="2026-03-01T00:00:00Z"),
            an_entity(key="corpus/b.md", permalink="b", updated_at="2026-03-02T00:00:00Z"),
        ]
    )
    assert len(backend.recent_entities("2026-01-01T00:00:00Z", None, 1)) == 1


def test_recent_entities_narrow_by_type_before_the_limit(backend):
    """`--type X --limit 1` must mean "the newest X", not "X among the newest"."""
    backend.upsert_entities(
        [
            an_entity(key="corpus/n.md", permalink="n", entity_type="note", updated_at="2026-03-02T00:00:00Z"),
            an_entity(key="corpus/t.md", permalink="t", entity_type="task", updated_at="2026-03-01T00:00:00Z"),
        ]
    )
    found = backend.recent_entities("2026-01-01T00:00:00Z", "task", 1)
    assert [ref.key for ref in found] == ["corpus/t.md"]


def test_count_recent_entities_ignores_the_limit_and_honours_the_type(backend):
    backend.upsert_entities(
        [
            an_entity(key="corpus/a.md", permalink="a", entity_type="note", updated_at="2026-03-01T00:00:00Z"),
            an_entity(key="corpus/b.md", permalink="b", entity_type="note", updated_at="2026-03-02T00:00:00Z"),
            an_entity(key="corpus/c.md", permalink="c", entity_type="task", updated_at="2026-03-03T00:00:00Z"),
        ]
    )
    assert backend.count_recent_entities("2026-01-01T00:00:00Z", None) == 3
    assert backend.count_recent_entities("2026-01-01T00:00:00Z", "note") == 2
    assert backend.count_recent_entities("2026-03-03T00:00:00Z", None) == 1


def test_hydrate_turns_ids_back_into_entities(backend):
    backend.upsert_entities([an_entity(key="corpus/a.md", permalink="a", title="Alpha")])
    entity_id = backend.indexed_files()["corpus/a.md"].entity_id
    hydrated = backend.hydrate([entity_id, entity_id + 999])
    assert list(hydrated) == [entity_id]
    assert hydrated[entity_id].title == "Alpha"


def test_hydrate_of_nothing(backend):
    assert backend.hydrate([]) == {}


def test_iter_indexed_text_feeds_the_chunker(backend):
    backend.upsert_entities([an_entity(key="corpus/a.md", permalink="a", title="T", content="T\nbody")])
    backend.rebuild_text_index()
    rows = list(backend.iter_indexed_text())
    assert len(rows) == 1
    entity_id, text = rows[0]
    assert entity_id == backend.indexed_files()["corpus/a.md"].entity_id
    assert text.startswith("T\n\n")
    assert "body" in text


# -- file catalog ---------------------------------------------------------


def test_catalog_upsert_is_idempotent_by_path(backend):
    first = backend.upsert_catalog_entry(a_catalog_entry(size_bytes=1))
    second = backend.upsert_catalog_entry(a_catalog_entry(size_bytes=2))
    assert first == second
    stored = backend.get_catalog_entry("drive/report.docx")
    assert stored is not None
    assert stored.size_bytes == 2


def test_catalog_get_by_id(backend):
    entry_id = backend.upsert_catalog_entry(a_catalog_entry())
    stored = backend.get_catalog_entry_by_id(entry_id)
    assert stored is not None
    assert stored.original_path == "drive/report.docx"
    assert backend.get_catalog_entry_by_id(entry_id + 999) is None


def test_catalog_missing_entry_is_none(backend):
    assert backend.get_catalog_entry("drive/nothing.docx") is None


def test_catalog_status_transitions(backend):
    backend.upsert_catalog_entry(a_catalog_entry())
    backend.set_catalog_status("drive/report.docx", "failed", None, "converter blew up")
    failed = backend.get_catalog_entry("drive/report.docx")
    assert failed is not None
    assert failed.conversion_status == "failed"
    assert failed.error == "converter blew up"

    backend.set_catalog_status("drive/report.docx", "converted", "out/report.md", None)
    converted = backend.get_catalog_entry("drive/report.docx")
    assert converted is not None
    assert converted.conversion_status == "converted"
    assert converted.output_path == "out/report.md"
    assert converted.error is None


def test_catalog_search_filters(backend):
    backend.upsert_catalog_entry(a_catalog_entry())
    backend.upsert_catalog_entry(
        a_catalog_entry(
            original_path="mail/deck.pptx",
            file_name="deck.pptx",
            extension=".pptx",
            extractor="mail",
            conversion_status="converted",
            modified_at="2026-02-01T00:00:00Z",
        )
    )
    assert len(backend.search_catalog(a_catalog_query())) == 2
    assert [e.extension for e in backend.search_catalog(a_catalog_query(extension=".pptx"))] == [".pptx"]
    assert [e.extension for e in backend.search_catalog(a_catalog_query(extension="pptx"))] == [".pptx"]
    assert [e.extractor for e in backend.search_catalog(a_catalog_query(extractor="mail"))] == ["mail"]
    assert [e.conversion_status for e in backend.search_catalog(a_catalog_query(status="pending"))] == ["pending"]
    assert [e.file_name for e in backend.search_catalog(a_catalog_query(name_contains="deck"))] == ["deck.pptx"]
    assert len(backend.search_catalog(a_catalog_query(modified_after="2026-01-15T00:00:00Z"))) == 1
    assert len(backend.search_catalog(a_catalog_query(limit=1))) == 1


def test_count_catalog_sees_past_the_limit(backend):
    """The number a capped listing cannot tell you: how many there were."""
    backend.upsert_catalog_entry(a_catalog_entry())
    backend.upsert_catalog_entry(
        a_catalog_entry(original_path="mail/deck.pptx", file_name="deck.pptx", extension=".pptx", extractor="mail")
    )
    assert len(backend.search_catalog(a_catalog_query(limit=1))) == 1
    assert backend.count_catalog(a_catalog_query(limit=1)) == 2
    assert backend.count_catalog(a_catalog_query(limit=1, extension=".pptx")) == 1


def test_catalog_removal(backend):
    backend.upsert_catalog_entry(a_catalog_entry())
    assert backend.remove_catalog_entry("drive/report.docx") is True
    assert backend.remove_catalog_entry("drive/report.docx") is False


def test_catalog_stats_covers_every_configured_state(backend, index_config):
    backend.upsert_catalog_entry(a_catalog_entry())
    backend.upsert_catalog_entry(
        a_catalog_entry(original_path="mail/deck.pptx", file_name="deck.pptx", conversion_status="converted")
    )
    stats = backend.catalog_stats()
    for state in index_config.catalog.conversion_states:
        assert state in stats
    assert stats["total"] == 2
    assert stats["pending"] == 1
    assert stats["converted"] == 1
    assert stats["skipped"] == 0
    assert sum(stats[s] for s in index_config.catalog.conversion_states) == stats["total"]
