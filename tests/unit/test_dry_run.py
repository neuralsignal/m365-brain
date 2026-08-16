"""Tests for dry-run probes against a real (mocked-transport) GraphClient.

Pins the ``client.get(..., params=None)`` call shape at both call sites
(auth check and extractor probe) so a signature regression cannot slip in.
"""

from __future__ import annotations

import re
from unittest.mock import patch

import pytest
import structlog.testing
from hypothesis import given
from hypothesis import strategies as st
from pytest_httpx import HTTPXMock

from m365_brain.dry_run import _DRY_RUN_PROBES, _dry_run_probe_path, dry_run
from m365_brain.sync import EXTRACTORS


class TestDryRun:
    def test_auth_check_and_probes_succeed(self, httpx_mock: HTTPXMock, full_config):
        httpx_mock.add_response(
            url=re.compile(r".*/me\?\$select=displayName,userPrincipalName"),
            json={"displayName": "Test User", "userPrincipalName": "test@example.com"},
        )
        httpx_mock.add_response(
            url=re.compile(r".*/me/mailFolders/Inbox/messages.*"),
            json={"value": [{"id": "1", "subject": "hi"}]},
        )
        httpx_mock.add_response(url=re.compile(r".*/me/calendarView.*"), json={"value": []})
        httpx_mock.add_response(url=re.compile(r".*/me/chats.*"), json={"value": []})

        dry_run(full_config, lambda: "test-token", ["email", "calendar", "teams_chats"])

    def test_probe_failure_exits_nonzero(self, httpx_mock: HTTPXMock, full_config):
        httpx_mock.add_response(
            url=re.compile(r".*/me\?\$select=displayName,userPrincipalName"),
            json={"displayName": "Test User", "userPrincipalName": "test@example.com"},
        )
        httpx_mock.add_response(
            url=re.compile(r".*/me/mailFolders/Inbox/messages.*"),
            status_code=403,
            json={"error": {"code": "ErrorAccessDenied", "message": "denied"}},
        )

        with pytest.raises(SystemExit):
            dry_run(full_config, lambda: "test-token", ["email"])

    def test_auth_failure_exits_nonzero(self, httpx_mock: HTTPXMock, full_config):
        httpx_mock.add_response(
            url=re.compile(r".*/me\?\$select=displayName,userPrincipalName"),
            status_code=403,
            json={"error": {"code": "ErrorAccessDenied", "message": "denied"}},
        )

        with pytest.raises(SystemExit):
            dry_run(full_config, lambda: "test-token", ["email"])

    def test_unknown_extractor_exits_nonzero(self, httpx_mock: HTTPXMock, full_config):
        httpx_mock.add_response(
            url=re.compile(r".*/me\?\$select=displayName,userPrincipalName"),
            json={"displayName": "Test User", "userPrincipalName": "test@example.com"},
        )

        with structlog.testing.capture_logs() as cap, pytest.raises(SystemExit):
            dry_run(full_config, lambda: "test-token", ["nonexistent"])

        unknown_logs = [e for e in cap if e["event"] == "cli.dry_run_probe_unknown"]
        assert len(unknown_logs) == 1
        assert unknown_logs[0]["name"] == "nonexistent"

    def test_disabled_extractor_skipped(self, httpx_mock: HTTPXMock, full_config):
        httpx_mock.add_response(
            url=re.compile(r".*/me\?\$select=displayName,userPrincipalName"),
            json={"displayName": "Test User", "userPrincipalName": "test@example.com"},
        )

        with structlog.testing.capture_logs() as cap:
            dry_run(full_config, lambda: "test-token", ["teams_channels"])

        skipped_logs = [e for e in cap if e["event"] == "cli.dry_run_probe_skipped"]
        assert len(skipped_logs) == 1
        assert skipped_logs[0]["name"] == "teams_channels"
        assert skipped_logs[0]["reason"] == "disabled"

    def test_no_probe_configured_skipped(self, httpx_mock: HTTPXMock, full_config):
        httpx_mock.add_response(
            url=re.compile(r".*/me\?\$select=displayName,userPrincipalName"),
            json={"displayName": "Test User", "userPrincipalName": "test@example.com"},
        )

        with (
            patch("m365_brain.dry_run._dry_run_probe_path", return_value=None),
            structlog.testing.capture_logs() as cap,
        ):
            dry_run(full_config, lambda: "test-token", ["email"])

        skipped_logs = [e for e in cap if e["event"] == "cli.dry_run_probe_skipped"]
        assert len(skipped_logs) == 1
        assert skipped_logs[0]["name"] == "email"
        assert skipped_logs[0]["reason"] == "no probe configured"


class TestDryRunProbePath:
    def test_calendar_returns_dynamic_path(self):
        path = _dry_run_probe_path("calendar")
        assert path is not None
        assert "/me/calendarView" in path
        assert "startDateTime=" in path
        assert "endDateTime=" in path

    def test_known_static_probes_return_path(self):
        for name in _DRY_RUN_PROBES:
            assert _dry_run_probe_path(name) is not None

    def test_unknown_extractor_returns_none(self):
        assert _dry_run_probe_path("nonexistent") is None

    @given(name=st.sampled_from(sorted(set(EXTRACTORS.keys()) & (set(_DRY_RUN_PROBES.keys()) | {"calendar"}))))
    def test_extractors_with_probes_always_return_string(self, name: str):
        result = _dry_run_probe_path(name)
        assert isinstance(result, str)
        assert result.startswith("/")
