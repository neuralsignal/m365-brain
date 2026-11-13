"""Due-computation, cursor bookkeeping, and the restart bug they exist to fix."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hypothesis import assume, given
from hypothesis import strategies as st

from m365_brain.schedule import (
    INDEX_UNIT,
    Unit,
    due,
    mark_failure,
    mark_success,
    next_attempt,
    next_wake,
    read_cursor,
    read_cursors,
    units_from_config,
    wait_seconds,
)
from m365_brain.state import CURSORS, InMemoryStateStore

NOW = datetime(2026, 8, 5, 12, 0, 0, tzinfo=UTC)

BASE = 300
CAP = 3600

_units = st.builds(
    Unit,
    name=st.text(min_size=1, max_size=6),
    interval_seconds=st.integers(min_value=1, max_value=86_400),
    backoff_base_seconds=st.just(BASE),
    backoff_max_seconds=st.just(CAP),
)


def _unit(name: str, interval_seconds: int) -> Unit:
    return Unit(name=name, interval_seconds=interval_seconds, backoff_base_seconds=BASE, backoff_max_seconds=CAP)


def _cursor(last_run_at: str | None) -> dict:
    return {"last_run_at": last_run_at, "last_success_at": None, "consecutive_failures": 0, "last_error": None}


def _ago(seconds: int) -> str:
    return (NOW - timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%SZ")


class TestDue:
    def test_a_unit_with_no_cursor_is_due(self):
        unit = _unit("email", 180)
        assert due([unit], {}, NOW) == [unit]

    def test_a_cursor_with_no_last_run_is_due(self):
        unit = _unit("email", 180)
        assert due([unit], {"email": _cursor(None)}, NOW) == [unit]

    def test_not_due_before_the_interval_elapses(self):
        unit = _unit("email", 180)
        assert due([unit], {"email": _cursor(_ago(179))}, NOW) == []

    def test_due_exactly_at_the_interval(self):
        unit = _unit("email", 180)
        assert due([unit], {"email": _cursor(_ago(180))}, NOW) == [unit]

    def test_due_after_the_interval(self):
        unit = _unit("email", 180)
        assert due([unit], {"email": _cursor(_ago(1000))}, NOW) == [unit]

    def test_order_follows_the_units_not_the_cursors(self):
        units = [_unit("a", 1), _unit("b", 1)]
        assert [u.name for u in due(units, {"b": _cursor(_ago(9)), "a": _cursor(_ago(9))}, NOW)] == ["a", "b"]

    def test_a_naive_timestamp_is_read_as_utc(self):
        """Reading it as local time would skew every interval by the offset."""
        unit = _unit("email", 180)
        naive = (NOW - timedelta(seconds=200)).replace(tzinfo=None).isoformat()
        assert due([unit], {"email": _cursor(naive)}, NOW) == [unit]

    @given(units=st.lists(_units, max_size=6, unique_by=lambda u: u.name))
    def test_no_cursors_means_everything_is_due(self, units):
        assert due(units, {}, NOW) == units

    @given(unit=_units, elapsed=st.integers(min_value=0, max_value=200_000))
    def test_due_iff_elapsed_reaches_the_interval(self, unit, elapsed):
        cursors = {unit.name: _cursor(_ago(elapsed))}
        assert bool(due([unit], cursors, NOW)) == (elapsed >= unit.interval_seconds)


class TestNextWake:
    def test_now_when_something_is_already_due(self):
        assert next_wake([_unit("a", 60)], {}, NOW) == NOW

    def test_now_when_there_are_no_units(self):
        assert next_wake([], {}, NOW) == NOW

    def test_the_earliest_of_several(self):
        units = [_unit("slow", 3600), _unit("fast", 300)]
        cursors = {"slow": _cursor(_ago(0)), "fast": _cursor(_ago(0))}
        assert next_wake(units, cursors, NOW) == NOW + timedelta(seconds=300)

    @given(units=st.lists(_units, min_size=1, max_size=6, unique_by=lambda u: u.name), elapsed=st.integers(0, 100_000))
    def test_never_in_the_past(self, units, elapsed):
        cursors = {unit.name: _cursor(_ago(elapsed)) for unit in units}
        assert next_wake(units, cursors, NOW) >= NOW

    @given(units=st.lists(_units, min_size=1, max_size=6, unique_by=lambda u: u.name), elapsed=st.integers(0, 100_000))
    def test_is_now_exactly_when_something_is_due(self, units, elapsed):
        cursors = {unit.name: _cursor(_ago(elapsed)) for unit in units}
        assert (next_wake(units, cursors, NOW) == NOW) == bool(due(units, cursors, NOW))

    @given(unit=_units, elapsed=st.integers(0, 100_000))
    def test_nothing_is_due_before_the_wake_time(self, unit, elapsed):
        cursors = {unit.name: _cursor(_ago(elapsed))}
        wake = next_wake([unit], cursors, NOW)
        assume(wake > NOW)
        assert due([unit], cursors, wake - timedelta(seconds=1)) == []
        assert due([unit], cursors, wake) == [unit]


class TestCursors:
    def test_success_advances_both_stamps(self):
        store = InMemoryStateStore()
        mark_success(store, "email", NOW)
        cursor = read_cursor(store, "email")
        assert cursor["last_run_at"] == cursor["last_success_at"] == "2026-08-05T12:00:00Z"
        assert cursor["consecutive_failures"] == 0
        assert cursor["consecutive_auth_failures"] == 0
        assert cursor["last_error"] is None

    def test_failure_advances_the_run_stamp_only(self):
        store = InMemoryStateStore()
        mark_success(store, "email", NOW - timedelta(hours=2))
        mark_failure(store, "email", NOW, "graph 500", False)
        cursor = read_cursor(store, "email")
        assert cursor["last_run_at"] == "2026-08-05T12:00:00Z"
        assert cursor["last_success_at"] == "2026-08-05T10:00:00Z"
        assert cursor["consecutive_failures"] == 1
        assert cursor["last_error"] == "graph 500"

    def test_failures_accumulate(self):
        store = InMemoryStateStore()
        for _ in range(3):
            mark_failure(store, "email", NOW, "boom", False)
        assert read_cursor(store, "email")["consecutive_failures"] == 3

    def test_a_success_clears_the_streak(self):
        store = InMemoryStateStore()
        mark_failure(store, "email", NOW, "boom", False)
        mark_failure(store, "email", NOW, "boom", False)
        mark_success(store, "email", NOW)
        assert read_cursor(store, "email")["consecutive_failures"] == 0

    def test_a_failure_still_stops_the_hot_loop(self):
        """The point of advancing `last_run_at` on failure."""
        store = InMemoryStateStore()
        unit = _unit("email", 180)
        mark_failure(store, "email", NOW, "graph 500", False)
        assert due([unit], read_cursors(store, [unit]), NOW) == []

    def test_an_unwritten_cursor_reads_as_empty(self):
        assert read_cursor(InMemoryStateStore(), "email") == {}

    def test_read_cursors_covers_every_unit(self):
        store = InMemoryStateStore()
        units = [_unit("email", 1), _unit("calendar", 1)]
        mark_success(store, "email", NOW)
        assert set(read_cursors(store, units)) == {"email", "calendar"}

    def test_cursors_survive_a_restart(self, tmp_path):
        """The regression the `schedule` library could not pass.

        A scheduler counting from process start restarts its clock with the
        process, so a six-hour unit under an hourly restart never runs.
        """
        from m365_brain.state import JsonStateStore

        unit = _unit("sharepoint", 6 * 3600)
        mark_success(JsonStateStore(tmp_path), "sharepoint", NOW - timedelta(hours=1))

        after_restart = JsonStateStore(tmp_path)
        assert due([unit], read_cursors(after_restart, [unit]), NOW) == []
        assert due([unit], read_cursors(after_restart, [unit]), NOW + timedelta(hours=6)) == [unit]

    def test_cursors_live_in_their_own_namespace(self):
        store = InMemoryStateStore()
        mark_success(store, "email", NOW)
        assert store.keys(CURSORS) == ["email"]


class TestUnitsFromConfig:
    def test_only_enabled_extractors_and_no_index_section_means_no_index_unit(self, vaulted_config):
        names = [unit.name for unit in units_from_config(vaulted_config)]
        assert names == ["email", "calendar", "teams_chats"]

    def test_the_index_is_a_unit_when_configured(self, runtime_config):
        units = {unit.name: unit for unit in units_from_config(runtime_config)}
        assert units[INDEX_UNIT].interval_seconds == runtime_config.index.sync.interval_minutes * 60

    def test_intervals_come_from_config_in_seconds(self, vaulted_config):
        units = {unit.name: unit for unit in units_from_config(vaulted_config)}
        assert units["email"].interval_seconds == vaulted_config.extractors.email.poll_interval_minutes * 60

    def test_a_disabled_extractor_is_absent_rather_than_flagged(self, vaulted_config):
        assert "sharepoint" not in [unit.name for unit in units_from_config(vaulted_config)]

    def test_unit_names_are_unique(self, runtime_config):
        names = [unit.name for unit in units_from_config(runtime_config)]
        assert len(names) == len(set(names))


def _failing(last_run_at: str, failures: int, auth_failures: int) -> dict:
    return {
        "last_run_at": last_run_at,
        "last_success_at": None,
        "consecutive_failures": failures,
        "consecutive_auth_failures": auth_failures,
        "last_error": "graph 503",
    }


_streaks = st.integers(min_value=0, max_value=200)


class TestWaitSeconds:
    def test_a_healthy_unit_waits_its_interval(self):
        assert wait_seconds(_unit("email", 600), 0) == 600

    def test_the_backoff_doubles_from_the_base(self):
        unit = _unit("email", 60)
        assert [wait_seconds(unit, n) for n in (1, 2, 3, 4, 5)] == [BASE, BASE * 2, BASE * 4, BASE * 8, CAP]

    def test_the_interval_is_the_floor(self):
        assert wait_seconds(_unit("sharepoint", 6 * 3600), 3) == 6 * 3600

    @given(unit=_units, streak=_streaks)
    def test_never_shorter_than_the_interval(self, unit, streak):
        assert wait_seconds(unit, streak) >= unit.interval_seconds

    @given(unit=_units, streak=_streaks)
    def test_never_longer_than_the_cap_or_the_interval(self, unit, streak):
        assert wait_seconds(unit, streak) <= max(unit.interval_seconds, CAP)

    @given(unit=_units, streak=_streaks)
    def test_non_decreasing_in_the_streak(self, unit, streak):
        assert wait_seconds(unit, streak + 1) >= wait_seconds(unit, streak)


class TestBackoff:
    def test_a_failing_unit_is_not_due_after_its_plain_interval(self):
        unit = _unit("email", 60)
        cursors = {"email": _failing(_ago(BASE * 4 - 1), 3, 0)}
        assert due([unit], cursors, NOW) == []

    def test_a_failing_unit_is_due_once_its_backoff_elapses(self):
        unit = _unit("email", 60)
        cursors = {"email": _failing(_ago(BASE * 4), 3, 0)}
        assert due([unit], cursors, NOW) == [unit]

    def test_other_units_keep_their_schedule(self):
        """The 2026-11-13 incident: mail down, OneDrive still has to sync."""
        email, onedrive = _unit("email", 60), _unit("onedrive", 60)
        cursors = {"email": _failing(_ago(120), 6, 0), "onedrive": _cursor(_ago(120))}
        assert due([email, onedrive], cursors, NOW) == [onedrive]

    def test_an_auth_failure_keeps_the_plain_interval(self):
        """Backing off after a needed login would only delay the first sync after it."""
        unit = _unit("email", 60)
        assert next_attempt(unit, _failing(_ago(0), 4, 1)) == NOW + timedelta(seconds=60)

    def test_next_attempt_is_none_before_the_first_run(self):
        assert next_attempt(_unit("email", 60), {}) is None

    @given(unit=_units, streak=_streaks, elapsed=st.integers(0, 100_000))
    def test_due_iff_the_backoff_has_elapsed(self, unit, streak, elapsed):
        cursors = {unit.name: _failing(_ago(elapsed), streak, 0)}
        assert bool(due([unit], cursors, NOW)) == (elapsed >= wait_seconds(unit, streak))


class TestAuthStreak:
    def test_an_auth_failure_counts_toward_the_auth_streak(self):
        store = InMemoryStateStore()
        mark_failure(store, "email", NOW, "login needed", True)
        mark_failure(store, "email", NOW, "login needed", True)
        cursor = read_cursor(store, "email")
        assert (cursor["consecutive_failures"], cursor["consecutive_auth_failures"]) == (2, 2)

    def test_a_non_auth_failure_resets_the_auth_streak_but_not_the_failure_streak(self):
        store = InMemoryStateStore()
        mark_failure(store, "email", NOW, "login needed", True)
        mark_failure(store, "email", NOW, "graph 503", False)
        cursor = read_cursor(store, "email")
        assert (cursor["consecutive_failures"], cursor["consecutive_auth_failures"]) == (2, 0)

    def test_a_success_resets_both(self):
        store = InMemoryStateStore()
        mark_failure(store, "email", NOW, "login needed", True)
        mark_success(store, "email", NOW)
        cursor = read_cursor(store, "email")
        assert (cursor["consecutive_failures"], cursor["consecutive_auth_failures"]) == (0, 0)


class TestBackoffFromConfig:
    def test_every_unit_carries_the_service_backoff(self, runtime_config):
        service = runtime_config.service
        for unit in units_from_config(runtime_config):
            assert (unit.backoff_base_seconds, unit.backoff_max_seconds) == (
                service.unit_backoff_base_seconds,
                service.unit_backoff_max_seconds,
            )
