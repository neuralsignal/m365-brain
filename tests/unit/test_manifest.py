"""Manifest models, the change recorder, and the on-disk store."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from m365_brain.config import ManifestConfig
from m365_brain.manifest import (
    ChangeManifest,
    ChangeRecorder,
    ExtractorChanges,
    FileChange,
    HookOutcome,
    IndexOutcome,
    ManifestStore,
    RecordingStorage,
    new_cycle_id,
)
from m365_brain.storage.base import StorageBackend
from m365_brain.storage.local import LocalBackend

NOW = datetime(2026, 8, 5, 10, 15, 3, tzinfo=UTC)


def _extractor(name: str, error: str | None, paths: list[tuple[str, str]]) -> ExtractorChanges:
    return ExtractorChanges(
        name=name,
        started_at=NOW,
        finished_at=NOW,
        item_count=len(paths),
        changes=[FileChange(path=path, kind=kind, record_ids=[]) for path, kind in paths],
        error=error,
    )


def _manifest(
    extractors: list[ExtractorChanges], index: IndexOutcome | None, hooks: list[HookOutcome]
) -> ChangeManifest:
    return ChangeManifest(
        cycle_id=new_cycle_id(NOW),
        started_at=NOW,
        finished_at=NOW,
        extractors=extractors,
        index=index,
        hooks=hooks,
    )


def _index(errors: int) -> IndexOutcome:
    return IndexOutcome(roots=["vault"], indexed=3, skipped=1, pruned=0, errors=errors, elapsed_seconds=0.5)


@pytest.fixture()
def store(tmp_path) -> ManifestStore:
    return ManifestStore(tmp_path / "manifests", ManifestConfig(retain_cycles=3, latest_filename="latest.json"))


class TestCycleId:
    def test_sorts_chronologically(self):
        earlier = new_cycle_id(datetime(2026, 8, 5, 10, 0, 0, tzinfo=UTC))
        later = new_cycle_id(datetime(2026, 8, 5, 10, 0, 1, tzinfo=UTC))
        assert earlier < later

    def test_two_ids_in_the_same_second_differ(self):
        assert new_cycle_id(NOW) != new_cycle_id(NOW)


class TestVerdict:
    def test_ok_when_nothing_failed(self):
        manifest = _manifest([_extractor("email", None, [("a.md", "added")])], _index(0), [])
        assert manifest.ok
        assert manifest.failures() == []

    def test_an_extractor_error_is_a_failure(self):
        manifest = _manifest([_extractor("email", "boom", [])], _index(0), [])
        assert not manifest.ok
        assert manifest.failures() == ["extractor email: boom"]

    def test_an_index_error_is_a_failure(self):
        manifest = _manifest([_extractor("email", None, [])], _index(2), [])
        assert not manifest.ok
        assert manifest.failures() == ["index: 2 file(s) failed to index"]

    def test_a_hook_error_is_a_failure(self):
        manifest = _manifest([], None, [HookOutcome(spec="pkg:on_cycle", error="kaboom")])
        assert not manifest.ok
        assert manifest.failures() == ["hook pkg:on_cycle: kaboom"]

    def test_a_succeeding_hook_is_not_a_failure(self):
        manifest = _manifest([], None, [HookOutcome(spec="pkg:on_cycle", error=None)])
        assert manifest.ok

    def test_every_failure_is_reported_not_just_the_first(self):
        manifest = _manifest(
            [_extractor("email", "boom", []), _extractor("calendar", "bang", [])],
            _index(1),
            [HookOutcome(spec="pkg:a", error="kaboom"), HookOutcome(spec="pkg:b", error=None)],
        )
        assert len(manifest.failures()) == 4

    def test_a_cycle_with_no_index_step_can_still_be_ok(self):
        assert _manifest([_extractor("email", None, [])], None, []).ok


class TestPaths:
    @pytest.fixture()
    def manifest(self) -> ChangeManifest:
        return _manifest(
            [
                _extractor("email", None, [("emails/a.md", "added"), ("emails/b.md", "updated")]),
                _extractor("calendar", None, [("calendar/c.md", "added"), ("calendar/d.md", "removed")]),
            ],
            None,
            [],
        )

    def test_everything(self, manifest):
        assert manifest.paths(kind=None, extractor=None) == [
            "emails/a.md",
            "emails/b.md",
            "calendar/c.md",
            "calendar/d.md",
        ]

    def test_by_kind(self, manifest):
        assert manifest.paths(kind="added", extractor=None) == ["emails/a.md", "calendar/c.md"]

    def test_by_extractor(self, manifest):
        assert manifest.paths(kind=None, extractor="calendar") == ["calendar/c.md", "calendar/d.md"]

    def test_by_both(self, manifest):
        assert manifest.paths(kind="removed", extractor="calendar") == ["calendar/d.md"]

    def test_an_unknown_extractor_is_empty_not_an_error(self, manifest):
        assert manifest.paths(kind=None, extractor="nope") == []


class TestModels:
    def test_frozen(self):
        change = FileChange(path="a.md", kind="added", record_ids=[])
        with pytest.raises(ValidationError):
            change.path = "b.md"

    def test_an_unknown_kind_is_rejected(self):
        with pytest.raises(ValidationError):
            FileChange(path="a.md", kind="modified", record_ids=[])

    def test_an_unknown_field_is_rejected(self):
        with pytest.raises(ValidationError):
            FileChange(path="a.md", kind="added", record_ids=[], sender="x@example.com")

    def test_record_ids_has_no_default(self):
        with pytest.raises(ValidationError):
            FileChange(path="a.md", kind="added")


class TestChangeRecorder:
    def test_starts_empty(self):
        assert ChangeRecorder().changes() == []

    def test_records_in_path_order(self):
        recorder = ChangeRecorder()
        recorder.record("z.md", "added")
        recorder.record("a.md", "added")
        assert [c.path for c in recorder.changes()] == ["a.md", "z.md"]

    def test_a_file_written_twice_appears_once_and_stays_added(self):
        recorder = ChangeRecorder()
        recorder.record("a.md", "added")
        recorder.record("a.md", "updated")
        assert [(c.path, c.kind) for c in recorder.changes()] == [("a.md", "added")]

    def test_a_removal_overrides_an_earlier_write(self):
        recorder = ChangeRecorder()
        recorder.record("a.md", "added")
        recorder.record("a.md", "removed")
        assert recorder.changes()[0].kind == "removed"

    def test_a_write_after_a_removal_wins_back(self):
        recorder = ChangeRecorder()
        recorder.record("a.md", "removed")
        recorder.record("a.md", "updated")
        assert recorder.changes()[0].kind == "updated"

    def test_record_ids_attach_to_their_path(self):
        recorder = ChangeRecorder()
        recorder.record("chat/messages.md", "updated")
        recorder.note_records("chat/messages.md", ["m2", "m1"])
        assert recorder.changes()[0].record_ids == ["m1", "m2"]

    def test_record_ids_accumulate_and_deduplicate(self):
        recorder = ChangeRecorder()
        recorder.record("chat/messages.md", "updated")
        recorder.note_records("chat/messages.md", ["m1"])
        recorder.note_records("chat/messages.md", ["m1", "m2"])
        assert recorder.changes()[0].record_ids == ["m1", "m2"]

    def test_ids_noted_before_the_write_still_land(self):
        recorder = ChangeRecorder()
        recorder.note_records("chat/messages.md", ["m1"])
        recorder.record("chat/messages.md", "added")
        assert recorder.changes()[0].record_ids == ["m1"]

    def test_ids_for_a_path_nothing_wrote_are_dropped(self):
        recorder = ChangeRecorder()
        recorder.note_records("chat/messages.md", ["m1"])
        assert recorder.changes() == []

    def test_other_changes_carry_no_record_ids(self):
        recorder = ChangeRecorder()
        recorder.record("emails/a.md", "added")
        assert recorder.changes()[0].record_ids == []


class TestRecordingStorage:
    @pytest.fixture()
    def wired(self, tmp_path) -> tuple[RecordingStorage, ChangeRecorder, LocalBackend]:
        inner = LocalBackend(str(tmp_path / "vault"))
        recorder = ChangeRecorder()
        return RecordingStorage(inner, recorder), recorder, inner

    def test_satisfies_the_storage_protocol(self, wired):
        storage, _, _ = wired
        assert isinstance(storage, StorageBackend)

    def test_a_first_write_is_added(self, wired):
        storage, recorder, _ = wired
        storage.write_file("a.md", "hello")
        assert [(c.path, c.kind) for c in recorder.changes()] == [("a.md", "added")]

    def test_a_second_write_to_an_existing_file_is_updated(self, wired):
        storage, recorder, inner = wired
        inner.write_file("a.md", "before")
        storage.write_file("a.md", "after")
        assert [(c.path, c.kind) for c in recorder.changes()] == [("a.md", "updated")]

    def test_bytes_are_recorded_too(self, wired):
        storage, recorder, _ = wired
        storage.write_bytes("attachments/x.pdf", b"%PDF")
        assert [(c.path, c.kind) for c in recorder.changes()] == [("attachments/x.pdf", "added")]

    def test_a_delete_is_recorded(self, wired):
        storage, recorder, inner = wired
        inner.write_file("a.md", "hello")
        storage.delete_file("a.md")
        assert [(c.path, c.kind) for c in recorder.changes()] == [("a.md", "removed")]

    def test_reads_are_delegated_and_not_recorded(self, wired):
        storage, recorder, inner = wired
        inner.write_file("a.md", "hello")
        assert storage.read_file("a.md") == "hello"
        assert storage.file_exists("a.md")
        assert storage.list_files("") == ["a.md"]
        assert recorder.changes() == []

    def test_a_failed_write_is_not_recorded(self, tmp_path):
        class Exploding:
            def write_file(self, path: str, content: str) -> None:
                raise OSError("disk full")

            def file_exists(self, path: str) -> bool:
                return False

        recorder = ChangeRecorder()
        with pytest.raises(OSError, match="disk full"):
            RecordingStorage(Exploding(), recorder).write_file("a.md", "x")
        assert recorder.changes() == []


class TestManifestStore:
    def test_nothing_before_the_first_cycle(self, store):
        assert store.latest() is None
        assert store.cycle_ids() == []
        assert store.prune() == []

    def test_round_trip(self, store):
        manifest = _manifest([_extractor("email", None, [("a.md", "added")])], _index(0), [])
        store.write(manifest)
        assert store.read(manifest.cycle_id) == manifest

    def test_datetimes_survive_the_round_trip(self, store):
        manifest = _manifest([], None, [])
        store.write(manifest)
        assert store.read(manifest.cycle_id).started_at == NOW

    def test_latest_matches_the_newest_write(self, store):
        first = _manifest([_extractor("email", None, [])], None, [])
        store.write(first)
        second = _manifest([_extractor("calendar", None, [])], None, [])
        store.write(second)
        assert store.latest() == second

    def test_latest_is_a_copy_not_a_symlink(self, store, tmp_path):
        store.write(_manifest([], None, []))
        assert not (tmp_path / "manifests" / "latest.json").is_symlink()

    def test_a_pruned_cycle_reads_as_none(self, store):
        manifest = _manifest([], None, [])
        assert store.read(manifest.cycle_id) is None

    def test_rewriting_the_same_cycle_replaces_it(self, store):
        manifest = _manifest([], None, [])
        store.write(manifest)
        with_hooks = manifest.model_copy(update={"hooks": [HookOutcome(spec="pkg:a", error=None)]})
        store.write(with_hooks)
        assert store.cycle_ids() == [manifest.cycle_id]
        assert len(store.read(manifest.cycle_id).hooks) == 1

    def test_prune_keeps_exactly_retain_cycles_newest(self, store):
        ids = []
        for minute in range(6):
            manifest = _manifest([], None, []).model_copy(update={"cycle_id": new_cycle_id(NOW.replace(minute=minute))})
            store.write(manifest)
            ids.append(manifest.cycle_id)
        removed = store.prune()
        assert removed == ids[:3]
        assert store.cycle_ids() == ids[3:]

    def test_prune_is_idempotent(self, store):
        for minute in range(5):
            store.write(
                _manifest([], None, []).model_copy(update={"cycle_id": new_cycle_id(NOW.replace(minute=minute))})
            )
        store.prune()
        assert store.prune() == []

    def test_prune_leaves_the_pointer_file_alone(self, store, tmp_path):
        for minute in range(5):
            store.write(
                _manifest([], None, []).model_copy(update={"cycle_id": new_cycle_id(NOW.replace(minute=minute))})
            )
        store.prune()
        assert (tmp_path / "manifests" / "latest.json").is_file()
        assert store.latest() is not None
