"""Tests for dry-run probes against a real (mocked-transport) GraphClient.

Pins the ``client.get(..., params=None)`` call shape at both call sites
(auth check and extractor probe) so a signature regression cannot slip in.
"""

from __future__ import annotations

import re

import pytest
from pytest_httpx import HTTPXMock

from m365_extract.dry_run import dry_run


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
