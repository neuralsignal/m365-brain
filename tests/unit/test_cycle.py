"""Unit tests for the continuous loop: run_forever, exhausted_units, report_backoff, _sleep_until."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, call, patch

import pytest
from structlog.testing import capture_logs

from m365_brain.config import ConfigError
from m365_brain.cycle import Runtime, Selection
from m365_brain.daemon import _sleep_until, exhausted_units, report_backoff, run_forever
from m365_brain.schedule import Unit, mark_failure, mark_success, read_cursors
from m365_brain.state import CURSORS, InMemoryStateStore


def _minimal_runtime(config, state) -> Runtime:
    return Runtime(
        config=config,
        vault=MagicMock(),
        storage=MagicMock(),
        state=state,
        manifests=MagicMock(),
        token_provider=lambda: "test-token",
        hooks=[],
    )


ONCE = Selection(names=None, resync=False, ignore_schedule=True)


class TestExhaustedUnits:
    def test_returns_names_whose_auth_streak_exceeds_ceiling(self) -> None:
        cursors = {"email": {"consecutive_auth_failures": 10}, "calendar": {"consecutive_auth_failures": 2}}
        assert exhausted_units(cursors, ceiling=5) == ["email"]

    def test_a_long_non_auth_streak_never_exhausts(self) -> None:
        """The 2026-11-13 regression: Exchange 5xx for hours must not stop the daemon."""
        cursors = {"email": {"consecutive_failures": 40, "consecutive_auth_failures": 0}}
        assert exhausted_units(cursors, ceiling=5) == []

    def test_a_cursor_written_before_the_auth_streak_existed_never_exhausts(self) -> None:
        """The persisted streak that exited every restart on 2026-11-13."""
        assert exhausted_units({"email": {"consecutive_failures": 10}}, ceiling=5) == []

    def test_returns_empty_when_no_cursors(self) -> None:
        assert exhausted_units({}, ceiling=5) == []


NOW = datetime(2026, 11, 13, 3, 0, tzinfo=UTC)
EMAIL = Unit(name="email", interval_seconds=600, backoff_base_seconds=300, backoff_max_seconds=3600)
ONEDRIVE = Unit(name="onedrive", interval_seconds=600, backoff_base_seconds=300, backoff_max_seconds=3600)


class TestReportBackoff:
    def test_one_event_per_unit_that_failed_this_cycle(self) -> None:
        state = InMemoryStateStore()
        for _ in range(3):
            mark_failure(state, "email", NOW, "graph 503", False)
        mark_success(state, "onedrive", NOW)

        with capture_logs() as logs:
            report_backoff([EMAIL, ONEDRIVE], read_cursors(state, [EMAIL, ONEDRIVE]), NOW)

        events = [entry for entry in logs if entry["event"] == "unit.backing_off"]
        assert len(events) == 1
        assert events[0]["unit"] == "email"
        assert events[0]["consecutive_failures"] == 3
        assert events[0]["next_attempt_at"] == (NOW + timedelta(seconds=1200)).isoformat()
        assert events[0]["last_error"] == "graph 503"
        assert events[0]["log_level"] == "error"

    def test_silent_for_a_failure_from_an_earlier_cycle(self) -> None:
        state = InMemoryStateStore()
        mark_failure(state, "email", NOW - timedelta(minutes=10), "graph 503", False)

        with capture_logs() as logs:
            report_backoff([EMAIL], read_cursors(state, [EMAIL]), NOW)

        assert not [entry for entry in logs if entry["event"] == "unit.backing_off"]


class TestSleepUntil:
    def test_returns_immediately_when_wake_is_in_the_past(self) -> None:
        with patch("m365_brain.daemon.time.sleep") as mock_sleep:
            _sleep_until(datetime.now(UTC) - timedelta(seconds=10), 30)
            mock_sleep.assert_not_called()

    def test_slices_into_bounded_chunks(self) -> None:
        origin = datetime(2026, 1, 1, tzinfo=UTC)

        class Clock(datetime):
            _now = origin

            @classmethod
            def now(cls, tz=None) -> datetime:
                return cls._now

        def advance(seconds: float) -> None:
            Clock._now = Clock._now + timedelta(seconds=seconds)

        wake = origin + timedelta(seconds=70)

        with (
            patch("m365_brain.daemon.datetime", Clock),
            patch("m365_brain.daemon.time.sleep", side_effect=advance) as mock_sleep,
        ):
            _sleep_until(wake, 30)

        assert mock_sleep.call_count == 3
        assert mock_sleep.call_args_list == [call(30), call(30), call(10.0)]


class TestRunForever:
    def test_delayed_start_sleeps_before_first_cycle(self, runtime_config) -> None:
        state = InMemoryStateStore()
        state.put(CURSORS, "email", {"consecutive_auth_failures": 10})
        rt = _minimal_runtime(runtime_config, state)

        with (
            patch("m365_brain.daemon.run_once"),
            patch("m365_brain.daemon.time.sleep") as mock_sleep,
            patch("m365_brain.daemon._sleep_until"),
        ):
            result = run_forever(rt, ONCE, delay_seconds=42)

        assert result == 1
        assert mock_sleep.call_args_list[0] == call(42)

    def test_exception_in_run_once_is_caught_and_loop_continues(self, runtime_config) -> None:
        state = InMemoryStateStore()
        rt = _minimal_runtime(runtime_config, state)

        with (
            patch(
                "m365_brain.daemon.run_once",
                side_effect=[RuntimeError("boom"), ConfigError("stop")],
            ) as mock_run,
            patch("m365_brain.daemon.time.sleep"),
            patch("m365_brain.daemon._sleep_until"),
            pytest.raises(ConfigError, match="stop"),
        ):
            run_forever(rt, ONCE, delay_seconds=0)

        assert mock_run.call_count == 2

    def test_returns_1_when_exhausted_units_found(self, runtime_config) -> None:
        state = InMemoryStateStore()
        state.put(CURSORS, "email", {"consecutive_auth_failures": 10})
        rt = _minimal_runtime(runtime_config, state)

        with (
            patch("m365_brain.daemon.run_once"),
            patch("m365_brain.daemon.time.sleep"),
            patch("m365_brain.daemon._sleep_until"),
        ):
            assert run_forever(rt, ONCE, delay_seconds=0) == 1

    def test_keeps_running_while_a_unit_fails_on_server_errors(self, runtime_config) -> None:
        state = InMemoryStateStore()
        state.put(CURSORS, "email", {"consecutive_failures": 10, "consecutive_auth_failures": 0})
        rt = _minimal_runtime(runtime_config, state)

        with (
            patch("m365_brain.daemon.run_once", side_effect=[None, ConfigError("escape")]) as mock_run,
            patch("m365_brain.daemon.time.sleep"),
            patch("m365_brain.daemon._sleep_until"),
            pytest.raises(ConfigError, match="escape"),
        ):
            run_forever(rt, ONCE, delay_seconds=0)

        assert mock_run.call_count == 2

    def test_continues_looping_when_no_unit_exhausted(self, runtime_config) -> None:
        state = InMemoryStateStore()
        rt = _minimal_runtime(runtime_config, state)

        with (
            patch(
                "m365_brain.daemon.run_once",
                side_effect=[None, ConfigError("escape")],
            ) as mock_run,
            patch("m365_brain.daemon.time.sleep"),
            patch("m365_brain.daemon._sleep_until"),
            pytest.raises(ConfigError, match="escape"),
        ):
            run_forever(rt, ONCE, delay_seconds=0)

        assert mock_run.call_count == 2
