"""One conformance suite, run against both `StateStore` implementations.

The suite is parametrized rather than duplicated because the fake exists to
keep the real one honest: an assertion that holds for only one of them has
found a difference, and a difference is exactly what the tests are for.
"""

from __future__ import annotations

import json

import pytest
from hypothesis import given
from hypothesis import strategies as st

from m365_brain.atomic_json import read_json, write_json
from m365_brain.state import CURSORS, CYCLES, EXTRACTOR_STATE, InMemoryStateStore, JsonStateStore, StateStore


@pytest.fixture(params=["json", "memory"])
def store(request, tmp_path) -> StateStore:
    if request.param == "json":
        return JsonStateStore(tmp_path / "state")
    return InMemoryStateStore()


class TestConformance:
    def test_absent_key_is_an_empty_dict_not_an_error(self, store):
        assert store.get(EXTRACTOR_STATE, "email") == {}

    def test_absent_namespace_has_no_keys(self, store):
        assert store.keys("never-written") == []

    def test_round_trip(self, store):
        store.put(EXTRACTOR_STATE, "email", {"delta": "abc", "count": 3})
        assert store.get(EXTRACTOR_STATE, "email") == {"delta": "abc", "count": 3}

    def test_put_replaces_rather_than_merges(self, store):
        store.put(EXTRACTOR_STATE, "email", {"delta": "abc", "count": 3})
        store.put(EXTRACTOR_STATE, "email", {"delta": "def"})
        assert store.get(EXTRACTOR_STATE, "email") == {"delta": "def"}

    def test_namespaces_are_independent(self, store):
        store.put(EXTRACTOR_STATE, "email", {"delta": "abc"})
        store.put(CURSORS, "email", {"last_run_at": "2026-08-05T10:00:00Z"})
        assert store.get(EXTRACTOR_STATE, "email") == {"delta": "abc"}
        assert store.get(CURSORS, "email") == {"last_run_at": "2026-08-05T10:00:00Z"}
        assert store.keys(CYCLES) == []

    def test_keys_are_sorted(self, store):
        for name in ("sharepoint", "email", "calendar"):
            store.put(EXTRACTOR_STATE, name, {"n": 1})
        assert store.keys(EXTRACTOR_STATE) == ["calendar", "email", "sharepoint"]

    def test_delete_removes_the_key(self, store):
        store.put(EXTRACTOR_STATE, "email", {"delta": "abc"})
        store.delete(EXTRACTOR_STATE, "email")
        assert store.get(EXTRACTOR_STATE, "email") == {}
        assert store.keys(EXTRACTOR_STATE) == []

    def test_delete_is_idempotent(self, store):
        store.delete(EXTRACTOR_STATE, "never-there")
        store.put(EXTRACTOR_STATE, "email", {"delta": "abc"})
        store.delete(EXTRACTOR_STATE, "email")
        store.delete(EXTRACTOR_STATE, "email")
        assert store.keys(EXTRACTOR_STATE) == []

    def test_delete_leaves_siblings_alone(self, store):
        store.put(EXTRACTOR_STATE, "email", {"delta": "a"})
        store.put(EXTRACTOR_STATE, "calendar", {"delta": "b"})
        store.delete(EXTRACTOR_STATE, "email")
        assert store.keys(EXTRACTOR_STATE) == ["calendar"]

    def test_a_stored_document_cannot_be_mutated_through(self, store):
        """Extractors mutate the dict they were handed; that must not persist."""
        store.put(EXTRACTOR_STATE, "email", {"delta": "abc"})
        handed_back = store.get(EXTRACTOR_STATE, "email")
        handed_back["path_map"] = {"x": "y"}
        assert store.get(EXTRACTOR_STATE, "email") == {"delta": "abc"}

    def test_the_caller_cannot_mutate_what_it_just_stored(self, store):
        payload = {"delta": "abc"}
        store.put(EXTRACTOR_STATE, "email", payload)
        payload["delta"] = "changed"
        assert store.get(EXTRACTOR_STATE, "email") == {"delta": "abc"}

    def test_satisfies_the_protocol(self, store):
        assert isinstance(store, StateStore)


class TestJsonStateStore:
    def test_one_file_per_namespace(self, tmp_path):
        store = JsonStateStore(tmp_path)
        store.put(EXTRACTOR_STATE, "email", {"delta": "abc"})
        store.put(CURSORS, "email", {"last_run_at": "x"})
        assert sorted(p.name for p in tmp_path.glob("*.json")) == ["cursors.json", "extractor_state.json"]

    def test_survives_a_new_process(self, tmp_path):
        JsonStateStore(tmp_path).put(EXTRACTOR_STATE, "email", {"delta": "abc"})
        assert JsonStateStore(tmp_path).get(EXTRACTOR_STATE, "email") == {"delta": "abc"}

    def test_creates_missing_parent_directories(self, tmp_path):
        store = JsonStateStore(tmp_path / "deep" / "nested")
        store.put(CYCLES, "20260805T100000Z-abc", {"ok": True})
        assert (tmp_path / "deep" / "nested" / "cycles.json").is_file()

    def test_leaves_no_temp_file_behind(self, tmp_path):
        store = JsonStateStore(tmp_path)
        store.put(EXTRACTOR_STATE, "email", {"delta": "abc"})
        assert list(tmp_path.glob("*.tmp")) == []

    def test_a_namespace_file_that_is_not_an_object_raises(self, tmp_path):
        (tmp_path / "cursors.json").write_text("[1, 2, 3]", encoding="utf-8")
        with pytest.raises(ValueError, match="not a JSON object"):
            JsonStateStore(tmp_path).get(CURSORS, "email")

    def test_unparseable_state_raises_rather_than_full_syncing(self, tmp_path):
        (tmp_path / "extractor_state.json").write_text("{not json", encoding="utf-8")
        with pytest.raises(json.JSONDecodeError):
            JsonStateStore(tmp_path).get(EXTRACTOR_STATE, "email")


class TestAtomicJson:
    def test_absent_file_reads_as_none(self, tmp_path):
        assert read_json(tmp_path / "nothing.json") is None

    def test_a_failed_serialisation_leaves_no_temp_file(self, tmp_path):
        target = tmp_path / "out.json"
        with pytest.raises(TypeError):
            write_json(target, {"bad": object()})
        assert not target.exists()
        assert list(tmp_path.glob("*.tmp")) == []

    def test_replacing_an_existing_file_keeps_it_readable(self, tmp_path):
        target = tmp_path / "out.json"
        write_json(target, {"n": 1})
        write_json(target, {"n": 2})
        assert read_json(target) == {"n": 2}

    @given(
        document=st.dictionaries(
            st.text(min_size=1, max_size=8),
            st.dictionaries(st.text(min_size=1, max_size=8), st.integers() | st.text(max_size=8), max_size=4),
            max_size=4,
        )
    )
    def test_round_trips_any_json_document(self, document, tmp_path_factory):
        target = tmp_path_factory.mktemp("atomic") / "doc.json"
        write_json(target, document)
        assert read_json(target) == document
