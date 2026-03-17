"""Tests for calendar extractor."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from pytest_httpx import HTTPXMock

from m365_extract.config import CalendarExtractorConfig, GraphConfig
from m365_extract.extractors import calendar
from m365_extract.graph_client import GraphClient
from m365_extract.storage.local import LocalBackend

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


@pytest.fixture()
def calendar_config():
    return CalendarExtractorConfig(
        enabled=True,
        poll_interval_minutes=60,
        lookback_days=30,
    )


@pytest.fixture()
def graph_config():
    return GraphConfig(
        max_retries=1,
        backoff_base_ms=10,
        timeout_seconds=5,
        max_pages=10,
    )


@pytest.fixture()
def calendar_response():
    return json.loads((FIXTURES_DIR / "calendar_response.json").read_text())


class TestCalendarExtractor:
    def test_sync_produces_markdown(
        self, httpx_mock: HTTPXMock, tmp_path, graph_config, calendar_config, calendar_response
    ):
        httpx_mock.add_response(
            url=re.compile(r".*/me/calendarView.*"),
            json=calendar_response,
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        state, count = calendar.run(client, storage, {}, calendar_config)

        assert count == 2
        assert "last_sync" in state
        assert state["events_fetched"] == 2

        files = storage.list_files("calendar")
        assert len(files) == 2
        client.close()

    def test_event_content_includes_details(
        self, httpx_mock: HTTPXMock, tmp_path, graph_config, calendar_config, calendar_response
    ):
        httpx_mock.add_response(
            url=re.compile(r".*/me/calendarView.*"),
            json=calendar_response,
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        calendar.run(client, storage, {}, calendar_config)

        files = storage.list_files("calendar")
        content = storage.read_file(files[0])

        assert "Weekly Team Standup" in content or "1:1 with Manager" in content
        client.close()

    def test_empty_calendar(self, httpx_mock: HTTPXMock, tmp_path, graph_config, calendar_config):
        httpx_mock.add_response(
            url=re.compile(r".*/me/calendarView.*"),
            json={"value": []},
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        state, count = calendar.run(client, storage, {}, calendar_config)
        assert count == 0
        assert state["events_fetched"] == 0
        client.close()

    def test_dates_with_trailing_zeros_preserved(self, httpx_mock: HTTPXMock, tmp_path, graph_config, calendar_config):
        """Dates like 2025-01-20T10:00:00.0000000 must not be corrupted."""
        response = {
            "value": [
                {
                    "id": "EVT-ZERO-TEST",
                    "subject": "Zero Date Meeting",
                    "start": {"dateTime": "2025-01-20T10:00:00.0000000", "timeZone": "UTC"},
                    "end": {"dateTime": "2025-01-20T10:30:00.0000000", "timeZone": "UTC"},
                    "location": {"displayName": ""},
                    "organizer": {"emailAddress": {"name": "Test", "address": "t@t.com"}},
                    "attendees": [],
                    "body": {"contentType": "text", "content": ""},
                    "type": "singleInstance",
                    "webLink": "",
                },
            ],
        }
        httpx_mock.add_response(
            url=re.compile(r".*/me/calendarView.*"),
            json=response,
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        state, count = calendar.run(client, storage, {}, calendar_config)
        assert count == 1

        files = storage.list_files("calendar")
        content = storage.read_file(files[0])
        # The date must be fully preserved, not truncated
        assert "2025-01-20T10:00:00Z" in content
        assert "2025-01-20T10:30:00Z" in content
        client.close()

    def test_recurring_event_tagged(
        self, httpx_mock: HTTPXMock, tmp_path, graph_config, calendar_config, calendar_response
    ):
        httpx_mock.add_response(
            url=re.compile(r".*/me/calendarView.*"),
            json=calendar_response,
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        calendar.run(client, storage, {}, calendar_config)

        files = storage.list_files("calendar")
        for f in files:
            content = storage.read_file(f)
            if "Weekly Team Standup" in content:
                assert "recurring" in content
                break
        client.close()
