"""Unit tests for run_forever, _exhausted_units, and _sleep_until."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, call, patch

import pytest

from m365_brain.config import ConfigError
from m365_brain.cycle import Runtime, Selection, _exhausted_units, _sleep_until, run_forever
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
    def test_returns_names_exceeding_ceiling(self, runtime_config) -> None:
        state = InMemoryStateStore()
        state.put(CURSORS, "email", {"consecutive_failures": 10})
        state.put(CURSORS, "calendar", {"consecutive_failures": 2})
        rt = _minimal_runtime(runtime_config, state)

        assert _exhausted_units(rt, ONCE, ceiling=5) == ["email"]

    def test_returns_empty_when_all_below(self, runtime_config) -> None:
        state = InMemoryStateStore()
        state.put(CURSORS, "email", {"consecutive_failures": 3})
        state.put(CURSORS, "calendar", {"consecutive_failures": 1})
        rt = _minimal_runtime(runtime_config, state)

        assert _exhausted_units(rt, ONCE, ceiling=5) == []

    def test_returns_empty_when_no_cursors(self, runtime_config) -> None:
        state = InMemoryStateStore()
        rt = _minimal_runtime(runtime_config, state)

        assert _exhausted_units(rt, ONCE, ceiling=5) == []


class TestSleepUntil:
    def test_returns_immediately_when_wake_is_in_the_past(self) -> None:
        with patch("m365_brain.cycle.time.sleep") as mock_sleep:
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
            patch("m365_brain.cycle.datetime", Clock),
            patch("m365_brain.cycle.time.sleep", side_effect=advance) as mock_sleep,
        ):
            _sleep_until(wake, 30)

        assert mock_sleep.call_count == 3
        assert mock_sleep.call_args_list == [call(30), call(30), call(10.0)]


class TestRunForever:
    def test_delayed_start_sleeps_before_first_cycle(self, runtime_config) -> None:
        state = InMemoryStateStore()
        state.put(CURSORS, "email", {"consecutive_failures": 10})
        rt = _minimal_runtime(runtime_config, state)

        with (
            patch("m365_brain.cycle.run_once"),
            patch("m365_brain.cycle.time.sleep") as mock_sleep,
            patch("m365_brain.cycle._sleep_until"),
        ):
            result = run_forever(rt, ONCE, delay_seconds=42)

        assert result == 1
        assert mock_sleep.call_args_list[0] == call(42)

    def test_exception_in_run_once_is_caught_and_loop_continues(self, runtime_config) -> None:
        state = InMemoryStateStore()
        rt = _minimal_runtime(runtime_config, state)

        with (
            patch(
                "m365_brain.cycle.run_once",
                side_effect=[RuntimeError("boom"), ConfigError("stop")],
            ) as mock_run,
            patch("m365_brain.cycle.time.sleep"),
            patch("m365_brain.cycle._sleep_until"),
            pytest.raises(ConfigError, match="stop"),
        ):
            run_forever(rt, ONCE, delay_seconds=0)

        assert mock_run.call_count == 2

    def test_returns_1_when_exhausted_units_found(self, runtime_config) -> None:
        state = InMemoryStateStore()
        state.put(CURSORS, "email", {"consecutive_failures": 10})
        rt = _minimal_runtime(runtime_config, state)

        with (
            patch("m365_brain.cycle.run_once"),
            patch("m365_brain.cycle.time.sleep"),
            patch("m365_brain.cycle._sleep_until"),
        ):
            assert run_forever(rt, ONCE, delay_seconds=0) == 1

    def test_continues_looping_when_no_unit_exhausted(self, runtime_config) -> None:
        state = InMemoryStateStore()
        rt = _minimal_runtime(runtime_config, state)

        with (
            patch(
                "m365_brain.cycle.run_once",
                side_effect=[None, ConfigError("escape")],
            ) as mock_run,
            patch("m365_brain.cycle.time.sleep"),
            patch("m365_brain.cycle._sleep_until"),
            pytest.raises(ConfigError, match="escape"),
        ):
            run_forever(rt, ONCE, delay_seconds=0)

        assert mock_run.call_count == 2
