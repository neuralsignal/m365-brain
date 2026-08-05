"""Incremental sync, against the fake backend only.

If any of these needs SQLite, the sync/backend split failed: the sync's job is
scanning, checksumming, ordering and permalink arbitration, none of which is a
property of where the result is stored.
"""

from __future__ import annotations

import pytest

from m365_brain.config.index import IndexConfig
from m365_brain.index.backends import create_index_backend
from m365_brain.index.sync import sync_index


def write(root, relative: str, text: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture()
def run(index_payload):
    """`run(full_rebuild)` over a backend that persists between calls."""
    index_payload["backend"] = "memory"
    config = IndexConfig.model_validate(index_payload)
    backend = create_index_backend(config)
    backend.initialize()

    def _run(full_rebuild: bool):
        return sync_index(config, backend, full_rebuild=full_rebuild)

    _run.backend = backend
    _run.config = config
    return _run


def test_indexes_every_markdown_file(run, corpus_root):
    write(corpus_root, "a.md", "# A\n")
    write(corpus_root, "nested/b.md", "# B\n")
    stats = run(full_rebuild=False)
    assert stats.total == 2
    assert stats.indexed == 2
    assert set(run.backend.indexed_files()) == {"corpus/a.md", "corpus/nested/b.md"}


def test_non_markdown_files_are_ignored(run, corpus_root):
    write(corpus_root, "a.md", "# A\n")
    write(corpus_root, "a.txt", "not indexed")
    assert run(full_rebuild=False).total == 1


def test_a_non_recursive_root_stays_shallow(index_payload, corpus_root):
    write(corpus_root, "a.md", "# A\n")
    write(corpus_root, "nested/b.md", "# B\n")
    index_payload["backend"] = "memory"
    index_payload["roots"][0]["recursive"] = False
    config = IndexConfig.model_validate(index_payload)
    backend = create_index_backend(config)
    backend.initialize()
    assert sync_index(config, backend, full_rebuild=False).total == 1


def test_two_roots_may_hold_the_same_relative_path(index_payload, tmp_path):
    first, second = tmp_path / "one", tmp_path / "two"
    for root in (first, second):
        write(root, "projects/x.md", "# X\n")
    index_payload["backend"] = "memory"
    index_payload["roots"] = [
        {"name": "one", "path": str(first), "recursive": True},
        {"name": "two", "path": str(second), "recursive": True},
    ]
    config = IndexConfig.model_validate(index_payload)
    backend = create_index_backend(config)
    backend.initialize()

    stats = sync_index(config, backend, full_rebuild=False)
    assert stats.indexed == 2
    assert set(backend.indexed_files()) == {"one/projects/x.md", "two/projects/x.md"}


def test_excluded_files_are_absent(index_payload, corpus_root):
    write(corpus_root, "keep.md", "# Keep\n")
    write(corpus_root, "_meta/skip.md", "# Skip\n")
    write(corpus_root, "deep/_meta/skip.md", "# Skip\n")
    index_payload["backend"] = "memory"
    index_payload["exclude"] = ["**/_meta/**"]
    config = IndexConfig.model_validate(index_payload)
    backend = create_index_backend(config)
    backend.initialize()

    sync_index(config, backend, full_rebuild=False)
    assert set(backend.indexed_files()) == {"corpus/keep.md"}


def test_an_unchanged_file_is_skipped_by_checksum(run, corpus_root):
    write(corpus_root, "a.md", "# A\n")
    run(full_rebuild=False)
    second = run(full_rebuild=False)
    assert second.indexed == 0
    assert second.skipped == 1


def test_a_changed_file_is_reindexed(run, corpus_root):
    write(corpus_root, "a.md", "# A\n")
    run(full_rebuild=False)
    write(corpus_root, "a.md", "# A\n\nmore\n")
    second = run(full_rebuild=False)
    assert second.indexed == 1
    assert second.skipped == 0


def test_full_rebuild_reparses_without_dropping_the_index(run, corpus_root):
    write(corpus_root, "a.md", "# A\n")
    run(full_rebuild=False)
    rebuilt = run(full_rebuild=True)
    assert rebuilt.indexed == 1
    assert rebuilt.skipped == 0
    assert set(run.backend.indexed_files()) == {"corpus/a.md"}


def test_a_deleted_file_is_pruned(run, corpus_root):
    write(corpus_root, "a.md", "# A\n")
    write(corpus_root, "b.md", "# B\n")
    run(full_rebuild=False)
    (corpus_root / "b.md").unlink()

    stats = run(full_rebuild=False)
    assert stats.pruned == 1
    assert set(run.backend.indexed_files()) == {"corpus/a.md"}


def test_a_renamed_file_keeps_its_permalink(run, corpus_root):
    """The prune-before-insert ordering. Reorder the phases and this fails:
    the insert would collide on a permalink the departing entity still owns."""
    write(corpus_root, "old.md", "---\ntitle: Stable\npermalink: stable\n---\nbody\n")
    run(full_rebuild=False)
    (corpus_root / "old.md").rename(corpus_root / "new.md")

    stats = run(full_rebuild=False)
    assert stats.pruned == 1
    assert run.backend.permalink_owners() == {"stable": "corpus/new.md"}


def test_a_genuine_collision_falls_back_to_a_path_derived_permalink(run, corpus_root):
    write(corpus_root, "one.md", "---\ntitle: A\npermalink: shared\n---\nbody\n")
    write(corpus_root, "nested/two.md", "---\ntitle: B\npermalink: shared\n---\nbody\n")
    run(full_rebuild=False)

    # Which of the two keeps `shared` follows scan order and is not the point;
    # that the loser gets a permalink derived from its (unique) key is.
    owners = run.backend.permalink_owners()
    assert set(owners.values()) == {"corpus/one.md", "corpus/nested/two.md"}
    assert "shared" in owners
    assert {"corpus-one", "corpus-nested-two"} & set(owners) != set()


def test_relations_are_resolved_and_the_text_index_is_rebuilt(run, corpus_root):
    write(corpus_root, "a.md", "---\ntitle: A\n---\n- knows [[B]]\n")
    write(corpus_root, "b.md", "---\ntitle: B\n---\nbody\n")
    stats = run(full_rebuild=False)
    assert stats.resolved == 1
    assert len(list(run.backend.iter_indexed_text())) == 2


def test_a_missing_root_names_itself(index_payload, tmp_path):
    index_payload["backend"] = "memory"
    index_payload["roots"] = [{"name": "absent", "path": str(tmp_path / "gone"), "recursive": True}]
    config = IndexConfig.model_validate(index_payload)
    backend = create_index_backend(config)
    backend.initialize()
    with pytest.raises(FileNotFoundError, match="absent"):
        sync_index(config, backend, full_rebuild=False)


def test_batching_does_not_change_the_result(index_payload, corpus_root):
    for n in range(5):
        write(corpus_root, f"{n}.md", f"# N{n}\n")
    index_payload["backend"] = "memory"
    index_payload["sync"]["batch_size"] = 2
    config = IndexConfig.model_validate(index_payload)
    backend = create_index_backend(config)
    backend.initialize()
    assert sync_index(config, backend, full_rebuild=False).indexed == 5
    assert len(backend.indexed_files()) == 5


def test_stats_report_elapsed_time(run, corpus_root):
    write(corpus_root, "a.md", "# A\n")
    assert run(full_rebuild=False).elapsed_seconds >= 0
