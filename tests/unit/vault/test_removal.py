"""Deleting what upstream deleted, and the idempotency the whole thing rests on.

The handler is small; what it has to be is *repeatable*. Upstream re-sends a
`@removed` marker for an id it already sent one for, so the second pass must be
a silent no-op rather than a 404. That contract lives on the storage backend's
`delete_file`, which is why there is a test asserting the real backend honours
it rather than only a fake.
"""

from __future__ import annotations

import pytest

from m365_brain.storage.local import LocalBackend
from m365_brain.vault.paths import VaultPaths
from m365_brain.vault.removal import PATH_MAP_STATE_KEY, RemovalHandler, purge_extractor


@pytest.fixture()
def storage(tmp_path) -> LocalBackend:
    return LocalBackend(str(tmp_path / "store"))


@pytest.fixture()
def handler(storage, paths) -> RemovalHandler:
    return RemovalHandler(storage=storage, paths=paths)


def write(storage: LocalBackend, path: str) -> str:
    storage.write_file(path, "# content\n")
    return path


class TestRemove:
    def test_it_deletes_the_recorded_file_and_drops_the_entry(self, storage, paths, handler):
        path = write(storage, paths.entry_file(paths.inbox_item("email", "x")))
        path_map = {"msg-1": path}

        assert handler.remove(extractor="email", upstream_id="msg-1", path_map=path_map) is True

        assert not storage.file_exists(path)
        assert path_map == {}

    def test_an_unknown_id_is_a_no_op_returning_false(self, handler):
        path_map: dict[str, str] = {}
        assert handler.remove(extractor="email", upstream_id="never-seen", path_map=path_map) is False

    def test_repeating_a_removal_is_a_clean_no_op(self, storage, paths, handler):
        """Upstream re-delivers `@removed`; a second pass must not raise."""
        path = write(storage, paths.entry_file(paths.inbox_item("email", "x")))
        path_map = {"msg-1": path}

        handler.remove(extractor="email", upstream_id="msg-1", path_map=path_map)
        assert handler.remove(extractor="email", upstream_id="msg-1", path_map=path_map) is False

    def test_it_survives_the_file_being_gone_already(self, paths, handler):
        """The map can outlive the file — an operator emptied the vault, a
        previous run crashed after the delete. Deleting again must not raise."""
        path_map = {"msg-1": paths.entry_file(paths.inbox_item("email", "never-written"))}

        assert handler.remove(extractor="email", upstream_id="msg-1", path_map=path_map) is True
        assert path_map == {}

    def test_it_only_removes_the_named_entry(self, storage, paths, handler):
        keep = write(storage, paths.entry_file(paths.inbox_item("email", "keep")))
        drop = write(storage, paths.entry_file(paths.inbox_item("email", "drop")))
        path_map = {"keep": keep, "drop": drop}

        handler.remove(extractor="email", upstream_id="drop", path_map=path_map)

        assert storage.file_exists(keep)
        assert path_map == {"keep": keep}


class TestBackendContract:
    def test_the_local_backend_treats_delete_file_as_idempotent(self, storage, paths):
        """Asserted against the real backend, not a fake. The handler relies on
        this; a backend that raised would turn every repeated `@removed` into a
        failed sync cycle."""
        path = write(storage, paths.entry_file(paths.inbox_item("email", "x")))
        storage.delete_file(path)
        storage.delete_file(path)  # must not raise
        assert not storage.file_exists(path)


class TestPurge:
    def test_it_removes_the_whole_inbox_subtree_and_the_state(self, storage, paths):
        for key in ("a", "b"):
            write(storage, paths.entry_file(paths.inbox_item("email", key)))
        state = {PATH_MAP_STATE_KEY: {"a": "x"}, "delta_link": "https://graph.example/delta"}

        removed = purge_extractor(storage, paths, state, "email")

        assert removed == 2
        assert storage.list_files(paths.inbox_root("email")) == []
        assert state == {}

    def test_it_leaves_other_extractors_alone(self, storage, paths):
        keep = write(storage, paths.entry_file(paths.inbox_item("contacts", "c")))
        write(storage, paths.entry_file(paths.inbox_item("email", "e")))

        purge_extractor(storage, paths, {}, "email")

        assert storage.file_exists(keep)

    def test_a_second_purge_is_a_no_op(self, storage, paths):
        write(storage, paths.entry_file(paths.inbox_item("email", "a")))
        purge_extractor(storage, paths, {}, "email")

        assert purge_extractor(storage, paths, {}, "email") == 0


class TestPaths:
    def test_the_handler_carries_the_resolver_so_purge_needs_no_literal(self, storage, vault_config):
        """`purge_extractor` derives its prefix from config, so an operator who
        renamed the email directory still gets the right subtree purged."""
        paths = VaultPaths(vault_config)
        handler = RemovalHandler(storage=storage, paths=paths)
        assert handler.paths.inbox_root("email") == "incoming/mail"
