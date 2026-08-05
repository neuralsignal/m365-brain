"""What is true of the fake alone.

Everything the fake shares with the real store is asserted in `test_base.py`
against both. What is left here is the fake's own contract: it claims substring
matching, not FTS5, and it holds nothing on disk.
"""

from __future__ import annotations

from m365_brain.index.backends.memory import InMemoryIndexBackend
from tests.unit.index.conftest import a_text_query, an_entity, make_backend


def memory(index_payload) -> InMemoryIndexBackend:
    return make_backend(index_payload, "memory")


def test_factory_returns_the_fake(index_payload):
    assert isinstance(memory(index_payload), InMemoryIndexBackend)


def test_nothing_is_written_to_disk(index_payload, tmp_path):
    backend = memory(index_payload)
    backend.upsert_entities([an_entity()])
    backend.rebuild_text_index()
    assert not (tmp_path / "index.db").exists()


def test_search_is_a_substring_scan_not_a_token_match(index_payload):
    """A prefix of a word matches here; FTS5 would need `tele*`.

    Asserted so that nobody writes a shared contract test relying on it.
    """
    backend = memory(index_payload)
    backend.upsert_entities([an_entity(content="A\ntelescope")])
    backend.rebuild_text_index()
    assert len(backend.text_search(a_text_query(fts="telesc")).hits) == 1


def test_search_is_case_insensitive(index_payload):
    backend = memory(index_payload)
    backend.upsert_entities([an_entity(content="A\nTelescope")])
    backend.rebuild_text_index()
    assert len(backend.text_search(a_text_query(fts="TELESCOPE")).hits) == 1


def test_fts_decoration_is_stripped_rather_than_matched(index_payload):
    """`TextQuery.fts` carries FTS5 syntax, which this store does not speak.

    Taking `telesc*` literally would match nothing for every query routed
    through `index/search.py` -- silence, not an error, which is the worse
    failure of the two.
    """
    backend = memory(index_payload)
    backend.upsert_entities([an_entity(content="A\ntelescope")])
    backend.rebuild_text_index()
    assert len(backend.text_search(a_text_query(fts="telesc*")).hits) == 1


def test_every_token_must_appear(index_payload):
    backend = memory(index_payload)
    backend.upsert_entities([an_entity(content="A\na telescope on a hill")])
    backend.rebuild_text_index()
    assert len(backend.text_search(a_text_query(fts="telescope* AND hill*")).hits) == 1
    assert backend.text_search(a_text_query(fts="telescope* AND submarine*")).hits == []


def test_boolean_operators_are_ignored_not_obeyed(index_payload):
    """`NOT` does not exclude here. Pinned so no shared contract test assumes it does."""
    backend = memory(index_payload)
    backend.upsert_entities([an_entity(content="A\na telescope")])
    backend.rebuild_text_index()
    assert len(backend.text_search(a_text_query(fts="telescope* NOT telescope*")).hits) == 1


def test_two_instances_share_nothing(index_payload):
    first = memory(index_payload)
    first.upsert_entities([an_entity()])
    assert memory(index_payload).indexed_files() == {}


def test_entity_ids_are_stable_across_an_upsert(index_payload):
    backend = memory(index_payload)
    backend.upsert_entities([an_entity(checksum="one")])
    first_id = backend.indexed_files()["corpus/note.md"].entity_id
    backend.upsert_entities([an_entity(checksum="two")])
    assert backend.indexed_files()["corpus/note.md"].entity_id == first_id


def test_setting_the_status_of_an_unknown_catalog_row_is_a_no_op(index_payload):
    backend = memory(index_payload)
    backend.set_catalog_status("nothing/here.docx", "converted", None, None)
    assert backend.catalog_stats()["total"] == 0
