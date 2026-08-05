"""One cycle, end to end, against fakes: state, storage, and recorded Graph.

The assertions that matter are the ones the PRD names: the manifest equals what
the cycle actually wrote, one failing extractor does not stop the others, and a
raising hook is logged without aborting the cycle. Everything else here is the
partial-failure table in `cycle.py`'s docstring, made executable.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

import pytest
from pytest_httpx import HTTPXMock

from m365_brain.config import ConfigError, HooksConfig
from m365_brain.cycle import Runtime, Selection, open_runtime, run_once, select_units
from m365_brain.manifest import ChangeManifest
from m365_brain.schedule import INDEX_UNIT, mark_success
from m365_brain.state import CURSORS, CYCLES, EXTRACTOR_STATE, InMemoryStateStore
from m365_brain.storage.local import LocalBackend
from m365_brain.vault.paths import manifest_directory
from tests.conftest import load_fixture
from tests.fixtures import hook_module

HOOKS_MODULE = "tests.fixtures.hook_module"


@pytest.fixture(autouse=True)
def clean_hook_module():
    hook_module.seen.clear()
    yield
    hook_module.seen.clear()


@pytest.fixture()
def graph(httpx_mock: HTTPXMock) -> HTTPXMock:
    """Every endpoint the enabled extractors reach, wired to a fixture.

    Reusable and non-asserting: several tests run more than one cycle, and
    several run a subset of the units, so "every registered response was
    requested exactly once" is the wrong contract here.
    """
    httpx_mock.add_response(
        url=re.compile(r".*/me/mailFolders/Inbox/messages/delta.*"),
        json=load_fixture("email_response.json"),
        is_reusable=True,
        is_optional=True,
    )
    httpx_mock.add_response(
        url=re.compile(r".*/me/calendarView.*"),
        json=load_fixture("calendar_response.json"),
        is_reusable=True,
        is_optional=True,
    )
    httpx_mock.add_response(url=re.compile(r".*/me/chats\?.*"), json={"value": []}, is_reusable=True, is_optional=True)
    return httpx_mock


@pytest.fixture()
def state() -> InMemoryStateStore:
    return InMemoryStateStore()


@pytest.fixture()
def runtime(runtime_config, state, tmp_path) -> Runtime:
    (tmp_path / "vault").mkdir(exist_ok=True)
    return open_runtime(runtime_config, LocalBackend(str(tmp_path / "vault")), state, lambda: "test-token")


def _hooked(runtime_config, state, tmp_path, specs: list[str]) -> Runtime:
    (tmp_path / "vault").mkdir(exist_ok=True)
    config = runtime_config.model_copy(update={"hooks": HooksConfig(post_cycle=specs, post_reconcile=[])})
    return open_runtime(config, LocalBackend(str(tmp_path / "vault")), state, lambda: "test-token")


ONCE = Selection(names=None, resync=False, ignore_schedule=True)


def _only(*names: str, resync: bool = False) -> Selection:
    return Selection(names=list(names), resync=resync, ignore_schedule=True)


class TestSelection:
    def test_defaults_to_every_enabled_unit(self, runtime_config):
        names = [unit.name for unit in select_units(runtime_config, None)]
        assert names == ["email", "calendar", "teams_chats", INDEX_UNIT]

    def test_narrows_to_the_named_units(self, runtime_config):
        assert [u.name for u in select_units(runtime_config, ["calendar"])] == ["calendar"]

    def test_an_unknown_name_is_a_config_error(self, runtime_config):
        with pytest.raises(ConfigError, match="unknown or disabled"):
            select_units(runtime_config, ["nope"])

    def test_a_disabled_extractor_is_a_config_error_not_a_silent_skip(self, runtime_config):
        with pytest.raises(ConfigError, match="unknown or disabled"):
            select_units(runtime_config, ["sharepoint"])

    def test_a_config_that_can_do_nothing_is_an_error(self, full_config, tmp_path):
        from tests.conftest import vault_section

        extractors = full_config.extractors.model_copy(
            update={
                name: getattr(full_config.extractors, name).model_copy(update={"enabled": False})
                for name in ("email", "calendar", "teams_chats")
            }
        )
        config = full_config.model_copy(update={"extractors": extractors, "vault": vault_section(tmp_path)})
        with pytest.raises(ConfigError, match="no units selected"):
            select_units(config, None)


class TestManifestMatchesReality:
    def test_the_manifest_equals_what_the_cycle_wrote(self, runtime, graph, runtime_config, tmp_path):
        """PRD acceptance criterion: the manifest is a complete record.

        Compared against the vault's content areas, not the whole tree: the
        cycle's own record lives under `vault.layout.meta`, and a manifest that
        listed itself would be a self-reference nothing could act on. The index
        excludes the same subtree for the same reason.
        """
        manifest = run_once(runtime, ONCE)
        meta = runtime_config.vault.layout.meta + "/"
        written = {path for path in LocalBackend(str(tmp_path / "vault")).list_files("") if not path.startswith(meta)}
        assert set(manifest.paths(kind=None, extractor=None)) == written
        assert written

    def test_the_manifest_does_not_list_itself(self, runtime, graph, runtime_config):
        meta = runtime_config.vault.layout.meta + "/"
        assert not any(p.startswith(meta) for p in run_once(runtime, ONCE).paths(kind=None, extractor=None))

    def test_each_change_is_attributed_to_its_extractor(self, runtime, graph):
        manifest = run_once(runtime, ONCE)
        assert all(path.startswith("inbox/emails/") for path in manifest.paths(kind=None, extractor="email"))
        assert all(path.startswith("inbox/calendar/") for path in manifest.paths(kind=None, extractor="calendar"))

    def test_a_first_run_records_everything_as_added(self, runtime, graph):
        manifest = run_once(runtime, ONCE)
        assert manifest.paths(kind="added", extractor=None) == manifest.paths(kind=None, extractor=None)

    def test_a_clean_cycle_is_ok(self, runtime, graph):
        assert run_once(runtime, ONCE).ok

    def test_item_counts_are_recorded_per_extractor(self, runtime, graph):
        manifest = run_once(runtime, ONCE)
        assert {e.name: e.item_count for e in manifest.extractors}["calendar"] == 2

    def test_the_manifest_is_on_disk_and_reads_back(self, runtime, graph, runtime_config):
        manifest = run_once(runtime, ONCE)
        assert runtime.manifests.read(manifest.cycle_id) == manifest
        assert runtime.manifests.latest() == manifest

    def test_manifests_live_under_the_configured_meta_directory(self, runtime, graph, runtime_config):
        run_once(runtime, ONCE)
        assert (manifest_directory(runtime_config.vault) / "latest.json").is_file()


class TestExtractorFailure:
    @pytest.fixture()
    def half_broken(self, httpx_mock: HTTPXMock) -> HTTPXMock:
        httpx_mock.add_response(
            url=re.compile(r".*/me/mailFolders/Inbox/messages/delta.*"),
            status_code=500,
            is_reusable=True,
            is_optional=True,
        )
        httpx_mock.add_response(
            url=re.compile(r".*/me/calendarView.*"),
            json=load_fixture("calendar_response.json"),
            is_reusable=True,
            is_optional=True,
        )
        return httpx_mock

    def test_the_other_extractor_still_runs(self, runtime, half_broken):
        manifest = run_once(runtime, _only("email", "calendar"))
        assert manifest.paths(kind=None, extractor="calendar")

    def test_the_cycle_completes_and_is_not_ok(self, runtime, half_broken):
        manifest = run_once(runtime, _only("email", "calendar"))
        assert not manifest.ok
        assert any("extractor email" in line for line in manifest.failures())

    def test_the_failure_is_recorded_on_that_extractor_only(self, runtime, half_broken):
        manifest = run_once(runtime, _only("email", "calendar"))
        errors = {entry.name: entry.error for entry in manifest.extractors}
        assert errors["email"] is not None
        assert errors["calendar"] is None

    def test_last_run_advances_but_last_success_does_not(self, runtime, state, half_broken):
        run_once(runtime, _only("email", "calendar"))
        cursor = state.get(CURSORS, "email")
        assert cursor["last_run_at"] is not None
        assert cursor["last_success_at"] is None
        assert cursor["consecutive_failures"] == 1

    def test_a_success_records_both_stamps(self, runtime, state, half_broken):
        run_once(runtime, _only("email", "calendar"))
        cursor = state.get(CURSORS, "calendar")
        assert cursor["last_success_at"] == cursor["last_run_at"]

    def test_every_extractor_failing_still_reaches_hooks(self, runtime_config, state, tmp_path, httpx_mock: HTTPXMock):
        httpx_mock.add_response(url=re.compile(r".*"), status_code=500, is_reusable=True, is_optional=True)
        runtime = _hooked(runtime_config, state, tmp_path, [f"{HOOKS_MODULE}:on_cycle"])
        manifest = run_once(runtime, _only("email", "calendar"))
        assert not manifest.ok
        assert len(hook_module.seen) == 1

    def test_changes_before_a_mid_run_failure_survive(self, runtime, state, httpx_mock: HTTPXMock):
        """The email extractor writes per folder; the second folder 500s."""
        httpx_mock.add_response(
            url=re.compile(r".*/me/mailFolders/Inbox/messages/delta.*"),
            json=load_fixture("email_response.json"),
            is_reusable=True,
            is_optional=True,
        )
        httpx_mock.add_response(
            url=re.compile(r".*/me/calendarView.*"), status_code=500, is_reusable=True, is_optional=True
        )
        manifest = run_once(runtime, _only("email", "calendar"))
        assert manifest.paths(kind=None, extractor="email")
        assert not manifest.ok

    def test_a_failed_extractor_keeps_its_delta_token(self, runtime, state, half_broken):
        state.put(EXTRACTOR_STATE, "email", {"delta_link": "keep-me"})
        run_once(runtime, _only("email", "calendar"))
        assert state.get(EXTRACTOR_STATE, "email") == {"delta_link": "keep-me"}


class TestHooks:
    def test_a_hook_receives_the_manifest(self, runtime_config, state, tmp_path, graph):
        runtime = _hooked(runtime_config, state, tmp_path, [f"{HOOKS_MODULE}:on_cycle"])
        manifest = run_once(runtime, ONCE)
        assert hook_module.seen == [f"on_cycle:{manifest.cycle_id}"]

    def test_a_raising_hook_is_logged_and_the_cycle_completes(self, runtime_config, state, tmp_path, graph, capsys):
        """PRD acceptance criterion."""
        runtime = _hooked(runtime_config, state, tmp_path, [f"{HOOKS_MODULE}:explodes"])
        manifest = run_once(runtime, ONCE)
        assert isinstance(manifest, ChangeManifest)
        captured = capsys.readouterr()
        assert "hook.failed" in captured.out + captured.err

    def test_a_raising_hook_makes_the_cycle_not_ok(self, runtime_config, state, tmp_path, graph):
        runtime = _hooked(runtime_config, state, tmp_path, [f"{HOOKS_MODULE}:explodes"])
        assert not run_once(runtime, ONCE).ok

    def test_a_raising_hook_does_not_stop_the_next(self, runtime_config, state, tmp_path, graph):
        runtime = _hooked(runtime_config, state, tmp_path, [f"{HOOKS_MODULE}:explodes", f"{HOOKS_MODULE}:on_cycle"])
        outcomes = run_once(runtime, ONCE).hooks
        assert [o.error is None for o in outcomes] == [False, True]

    def test_the_hook_outcome_is_persisted(self, runtime_config, state, tmp_path, graph):
        runtime = _hooked(runtime_config, state, tmp_path, [f"{HOOKS_MODULE}:explodes"])
        manifest = run_once(runtime, ONCE)
        assert runtime.manifests.read(manifest.cycle_id).hooks[0].error == "hook blew up"

    def test_the_extraction_record_exists_before_hooks_fire(self, runtime_config, state, tmp_path, graph):
        """Kill the process inside a hook and the record is still on disk."""
        seen: list[ChangeManifest | None] = []
        runtime = _hooked(runtime_config, state, tmp_path, [])
        store = runtime.manifests

        def peek(manifest):
            seen.append(store.latest())

        runtime = Runtime(**{**runtime.__dict__, "hooks": _inline_hook(peek)})
        manifest = run_once(runtime, ONCE)
        assert seen[0] is not None
        assert seen[0].cycle_id == manifest.cycle_id
        assert seen[0].extractors

    def test_an_unresolvable_hook_fails_at_open_not_at_fire(self, runtime_config, state, tmp_path):
        from m365_brain.hooks import HookResolutionError

        with pytest.raises(HookResolutionError):
            _hooked(runtime_config, state, tmp_path, ["no_such_package:on_cycle"])


def _inline_hook(call):
    from m365_brain.hooks import ResolvedHook

    return [ResolvedHook(spec="test:inline", call=call)]


class TestIndexStep:
    def test_runs_when_something_changed(self, runtime, graph):
        manifest = run_once(runtime, ONCE)
        assert manifest.index is not None
        assert manifest.index.indexed > 0

    def test_reports_the_configured_roots(self, runtime, graph):
        assert run_once(runtime, ONCE).index.roots == ["vault"]

    def test_skipped_when_nothing_changed_and_the_unit_is_not_due(self, runtime, state, graph):
        run_once(runtime, ONCE)
        now = datetime.now(UTC)
        for name in ("email", "calendar", "teams_chats", INDEX_UNIT):
            mark_success(state, name, now)
        second = run_once(runtime, Selection(names=None, resync=False, ignore_schedule=False))
        assert second.index is None
        assert second.extractors == []

    def test_runs_when_the_index_unit_is_due_even_with_no_extraction(self, runtime, state, graph, tmp_path):
        """A root nobody extracts into is still indexed, on its own interval."""
        run_once(runtime, ONCE)
        now = datetime.now(UTC)
        for name in ("email", "calendar", "teams_chats"):
            mark_success(state, name, now)
        mark_success(state, INDEX_UNIT, now - timedelta(hours=2))
        (tmp_path / "vault" / "handwritten.md").write_text("---\ntitle: By hand\n---\nbody\n", encoding="utf-8")

        second = run_once(runtime, Selection(names=None, resync=False, ignore_schedule=False))
        assert second.extractors == []
        assert second.index is not None
        assert second.index.indexed == 1

    def test_absent_when_the_index_is_not_selected(self, runtime, graph):
        assert run_once(runtime, _only("calendar")).index is None

    def test_an_index_failure_does_not_stop_the_cycle(self, runtime_config, state, tmp_path, graph):
        broken = runtime_config.model_copy(
            update={
                "index": runtime_config.index.model_copy(
                    update={
                        "roots": [runtime_config.index.roots[0].model_copy(update={"path": str(tmp_path / "gone")})]
                    }
                )
            }
        )
        (tmp_path / "vault").mkdir(exist_ok=True)
        runtime = open_runtime(broken, LocalBackend(str(tmp_path / "vault")), state, lambda: "t")
        manifest = run_once(runtime, ONCE)
        assert manifest.index.errors == 1
        assert not manifest.ok
        assert manifest.extractors


class TestResync:
    def test_clears_the_selected_extractors_state(self, runtime, state, graph):
        state.put(EXTRACTOR_STATE, "calendar", {"delta_link": "old"})
        run_once(runtime, _only("calendar", resync=True))
        assert state.get(EXTRACTOR_STATE, "calendar") != {"delta_link": "old"}

    def test_leaves_unselected_extractors_alone(self, runtime, state, graph):
        state.put(EXTRACTOR_STATE, "email", {"delta_link": "keep"})
        run_once(runtime, _only("calendar", resync=True))
        assert state.get(EXTRACTOR_STATE, "email") == {"delta_link": "keep"}

    def test_is_logged_loudly(self, runtime, state, graph, capsys):
        run_once(runtime, _only("calendar", resync=True))
        captured = capsys.readouterr()
        assert "cycle.state_cleared" in captured.out + captured.err


class TestCycleHistory:
    def test_a_summary_is_recorded_per_cycle(self, runtime, graph, state):
        manifest = run_once(runtime, ONCE)
        summary = state.get(CYCLES, manifest.cycle_id)
        assert summary["ok"] is True
        assert summary["changes"] == len(manifest.paths(kind=None, extractor=None))

    def test_older_manifests_are_pruned_to_the_configured_retention(self, runtime, graph, state):
        for _ in range(5):
            state.put(CURSORS, "email", {})
            run_once(runtime, ONCE)
        assert len(runtime.manifests.cycle_ids()) == 3
