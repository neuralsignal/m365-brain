"""Tests for m365_admin.daemon_runner module."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import pytest


@pytest.mark.admin
class TestDaemonRunner:
    def test_start_and_stop(self, tmp_path):
        """start_daemon runs at least one cycle then stops when event is set."""
        from m365_admin.daemon_runner import start_daemon

        mock_config = MagicMock()
        mock_engine = MagicMock()
        mock_adapter = MagicMock()
        state_dir = str(tmp_path / "state")

        with (
            patch("m365_admin.daemon_runner.run_daemon_cycle") as mock_cycle,
            patch("m365_admin.daemon_runner.write_health_file"),
        ):
            mock_cycle.return_value = []
            stop = start_daemon(mock_config, mock_engine, mock_adapter, state_dir, interval=1)

            # Wait for at least one cycle
            time.sleep(0.5)
            stop.set()
            time.sleep(0.5)

        assert mock_cycle.call_count >= 1
        assert mock_cycle.call_args[0][0] is mock_config
        assert mock_cycle.call_args[0][1] is mock_engine
        assert mock_cycle.call_args[0][2] is mock_adapter
        assert mock_cycle.call_args[0][3] == state_dir

    def test_stop_daemon_sets_event(self):
        """stop_daemon sets the threading.Event."""
        from m365_admin.daemon_runner import stop_daemon

        event = threading.Event()
        assert not event.is_set()
        stop_daemon(event)
        assert event.is_set()

    def test_cycle_exception_does_not_kill_thread(self, tmp_path):
        """If run_daemon_cycle raises, the thread continues."""
        from m365_admin.daemon_runner import start_daemon

        mock_config = MagicMock()
        mock_engine = MagicMock()
        mock_adapter = MagicMock()
        state_dir = str(tmp_path / "state")

        call_count = 0

        def side_effect(*args):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("test error")
            return []

        with (
            patch("m365_admin.daemon_runner.run_daemon_cycle") as mock_cycle,
            patch("m365_admin.daemon_runner.write_health_file"),
        ):
            mock_cycle.side_effect = side_effect
            stop = start_daemon(mock_config, mock_engine, mock_adapter, state_dir, interval=1)

            time.sleep(2.5)
            stop.set()
            time.sleep(0.5)

        # Thread survived the first exception and ran at least a second cycle
        assert call_count >= 2

    def test_creates_state_dir(self, tmp_path):
        """start_daemon creates the state directory if it doesn't exist."""
        from m365_admin.daemon_runner import start_daemon

        state_dir = str(tmp_path / "nonexistent" / "state")

        with (
            patch("m365_admin.daemon_runner.run_daemon_cycle") as mock_cycle,
            patch("m365_admin.daemon_runner.write_health_file"),
        ):
            mock_cycle.return_value = []
            stop = start_daemon(MagicMock(), MagicMock(), MagicMock(), state_dir, interval=1)

            time.sleep(0.5)
            stop.set()

        from pathlib import Path

        assert Path(state_dir).exists()
