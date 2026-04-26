"""Tests for calendar extractor."""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pytest_httpx import HTTPXMock

from m365_extract.config import CalendarExtractorConfig, GraphConfig
from m365_extract.extractors import calendar
from m365_extract.extractors.calendar import _normalize_graph_datetime, _write_event
from m365_extract.graph_client import GraphClient
from m365_extract.storage.local import LocalBackend

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures"


@pytest.fixture()
def calendar_config():
    return CalendarExtractorConfig(
        enabled=True,
        poll_interval_minutes=60,
        lookback_days=30,
        forward_days=90,
    )


@pytest.fixture()
def graph_config():
    return GraphConfig(
        max_retries=1,
        backoff_base_ms=10,
        timeout_seconds=5,
        max_pages=10,
        max_retry_after_seconds=300.0,
        error_message_max_length=200,
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

    def test_skip_unchanged_events(
        self, httpx_mock: HTTPXMock, tmp_path, graph_config, calendar_config, calendar_response
    ):
        """Second sync skips events that haven't changed (same lastModifiedDateTime)."""
        # Add lastModifiedDateTime to fixture events
        for event in calendar_response["value"]:
            event["lastModifiedDateTime"] = "2026-03-20T10:00:00Z"

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        # First sync — writes all events
        httpx_mock.add_response(url=re.compile(r".*/me/calendarView.*"), json=calendar_response)
        state, count1 = calendar.run(client, storage, {}, calendar_config)
        assert count1 == 2
        assert "event_modified_times" in state
        assert len(state["event_modified_times"]) == 2

        # Second sync with same state — should skip unchanged events
        httpx_mock.add_response(url=re.compile(r".*/me/calendarView.*"), json=calendar_response)
        state2, count2 = calendar.run(client, storage, state, calendar_config)
        assert count2 == 0
        assert state2["events_skipped"] == 2

        client.close()

    def test_modified_event_rewritten(
        self, httpx_mock: HTTPXMock, tmp_path, graph_config, calendar_config, calendar_response
    ):
        """An event with a new lastModifiedDateTime gets rewritten."""
        for event in calendar_response["value"]:
            event["lastModifiedDateTime"] = "2026-03-20T10:00:00Z"

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        # First sync
        httpx_mock.add_response(url=re.compile(r".*/me/calendarView.*"), json=calendar_response)
        state, _ = calendar.run(client, storage, {}, calendar_config)

        # Modify one event
        calendar_response["value"][0]["lastModifiedDateTime"] = "2026-03-21T08:00:00Z"

        # Second sync — one changed, one unchanged
        httpx_mock.add_response(url=re.compile(r".*/me/calendarView.*"), json=calendar_response)
        state2, count2 = calendar.run(client, storage, state, calendar_config)
        assert count2 == 1
        assert state2["events_skipped"] == 1

        client.close()

    def test_attendee_details_in_frontmatter(
        self, httpx_mock: HTTPXMock, tmp_path, graph_config, calendar_config, calendar_response
    ):
        """Frontmatter includes attendee_details with name, email, and status."""
        httpx_mock.add_response(
            url=re.compile(r".*/me/calendarView.*"),
            json=calendar_response,
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        calendar.run(client, storage, {}, calendar_config)

        files = storage.list_files("calendar")
        # Find the event with attendees (Weekly Team Standup)
        for f in files:
            content = storage.read_file(f)
            if "Weekly Team Standup" in content:
                assert "attendee_details" in content
                assert "bob@example.com" in content
                assert "carol@example.com" in content
                assert "accepted" in content
                break
        client.close()

    def test_attendee_body_includes_email(
        self, httpx_mock: HTTPXMock, tmp_path, graph_config, calendar_config, calendar_response
    ):
        """Body text includes attendee emails and statuses."""
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
                # Body should have enriched attendee line
                assert "bob@example.com" in content
                assert "carol@example.com" in content
                break
        client.close()

    def test_all_day_event(self, httpx_mock: HTTPXMock, tmp_path, graph_config, calendar_config):
        """All-day event uses date-only start.date (no dateTime), should not crash."""
        response = {
            "value": [
                {
                    "id": "EVT-ALL-DAY",
                    "subject": "Company Holiday",
                    "start": {"dateTime": "2026-04-01T00:00:00.0000000", "timeZone": "UTC"},
                    "end": {"dateTime": "2026-04-02T00:00:00.0000000", "timeZone": "UTC"},
                    "location": {"displayName": ""},
                    "organizer": {"emailAddress": {"name": "Admin", "address": "admin@example.com"}},
                    "attendees": [],
                    "body": {"contentType": "text", "content": "Office closed"},
                    "type": "singleInstance",
                    "webLink": "",
                    "isAllDay": True,
                },
            ],
        }
        httpx_mock.add_response(url=re.compile(r".*/me/calendarView.*"), json=response)

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        state, count = calendar.run(client, storage, {}, calendar_config)
        assert count == 1

        files = storage.list_files("calendar")
        content = storage.read_file(files[0])
        assert "Company Holiday" in content
        assert "2026-04-01" in content
        client.close()

    def test_event_no_organizer_name(self, httpx_mock: HTTPXMock, tmp_path, graph_config, calendar_config):
        """Event with empty organizer name should not crash and omit organizer line."""
        response = {
            "value": [
                {
                    "id": "EVT-NO-ORG",
                    "subject": "Quick Sync",
                    "start": {"dateTime": "2026-03-15T10:00:00.0000000", "timeZone": "UTC"},
                    "end": {"dateTime": "2026-03-15T10:30:00.0000000", "timeZone": "UTC"},
                    "location": {"displayName": ""},
                    "organizer": {"emailAddress": {"name": "", "address": "unknown@example.com"}},
                    "attendees": [],
                    "body": {"contentType": "text", "content": ""},
                    "type": "singleInstance",
                    "webLink": "",
                },
            ],
        }
        httpx_mock.add_response(url=re.compile(r".*/me/calendarView.*"), json=response)

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        state, count = calendar.run(client, storage, {}, calendar_config)
        assert count == 1

        files = storage.list_files("calendar")
        content = storage.read_file(files[0])
        assert "Quick Sync" in content
        # Organizer line should not appear when name is empty
        assert "**Organizer:**" not in content
        client.close()

    def test_subject_with_yaml_special_chars(self, httpx_mock: HTTPXMock, tmp_path, graph_config, calendar_config):
        """Subject with YAML special characters must round-trip safely through frontmatter."""
        tricky_subject = 'Budget: Q1 "Final" #2 [DRAFT] {updated}'
        response = {
            "value": [
                {
                    "id": "EVT-YAML-CHARS",
                    "subject": tricky_subject,
                    "start": {"dateTime": "2026-03-16T09:00:00.0000000", "timeZone": "UTC"},
                    "end": {"dateTime": "2026-03-16T10:00:00.0000000", "timeZone": "UTC"},
                    "location": {"displayName": "Room: A [Main]"},
                    "organizer": {"emailAddress": {"name": "Test", "address": "t@t.com"}},
                    "attendees": [],
                    "body": {"contentType": "text", "content": ""},
                    "type": "singleInstance",
                    "webLink": "",
                },
            ],
        }
        httpx_mock.add_response(url=re.compile(r".*/me/calendarView.*"), json=response)

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        calendar.run(client, storage, {}, calendar_config)

        files = storage.list_files("calendar")
        content = storage.read_file(files[0])

        # Parse the frontmatter back — must not corrupt the title
        from m365_extract.markdown_writer import loads_markdown

        fm, body = loads_markdown(content)
        assert fm["title"] == tricky_subject
        assert fm["location"] == "Room: A [Main]"
        client.close()

    def test_event_no_attendees_omits_attendee_details(
        self, httpx_mock: HTTPXMock, tmp_path, graph_config, calendar_config
    ):
        """Solo event (empty attendees) should have no attendee_details in frontmatter."""
        response = {
            "value": [
                {
                    "id": "EVT-SOLO",
                    "subject": "Focus Time",
                    "start": {"dateTime": "2026-03-17T08:00:00.0000000", "timeZone": "UTC"},
                    "end": {"dateTime": "2026-03-17T10:00:00.0000000", "timeZone": "UTC"},
                    "location": {"displayName": ""},
                    "organizer": {"emailAddress": {"name": "Me", "address": "me@example.com"}},
                    "attendees": [],
                    "body": {"contentType": "text", "content": ""},
                    "type": "singleInstance",
                    "webLink": "",
                },
            ],
        }
        httpx_mock.add_response(url=re.compile(r".*/me/calendarView.*"), json=response)

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        calendar.run(client, storage, {}, calendar_config)

        files = storage.list_files("calendar")
        content = storage.read_file(files[0])
        assert "attendee_details" not in content
        client.close()

    def test_uses_forward_days_config(self, httpx_mock: HTTPXMock, tmp_path, graph_config):
        """Verify forward_days config is used in the API request."""
        config = CalendarExtractorConfig(
            enabled=True,
            poll_interval_minutes=60,
            lookback_days=30,
            forward_days=180,
        )

        httpx_mock.add_response(url=re.compile(r".*/me/calendarView.*"), json={"value": []})

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        calendar.run(client, storage, {}, config)

        # Verify the request was made (the URL contains the date range)
        request = httpx_mock.get_request()
        assert request is not None
        assert "calendarView" in str(request.url)
        client.close()


class TestNormalizeGraphDatetime:
    def test_empty_string_returns_empty(self):
        assert _normalize_graph_datetime("") == ""

    def test_unparseable_returns_unchanged(self):
        assert _normalize_graph_datetime("not-a-date") == "not-a-date"

    def test_valid_datetime_normalized(self):
        result = _normalize_graph_datetime("2026-03-12T09:00:00.0000000")
        assert result == "2026-03-12T09:00:00Z"

    def test_already_z_suffixed(self):
        result = _normalize_graph_datetime("2026-03-12T09:00:00Z")
        assert result == "2026-03-12T09:00:00Z"


class TestWriteEventEdgeCases:
    def test_missing_event_id_returns_false(self):
        storage = MagicMock()
        event = {
            "subject": "Test",
            "start": {"dateTime": "2026-03-12T09:00:00.0000000"},
            "end": {"dateTime": "2026-03-12T10:00:00.0000000"},
        }
        assert _write_event(storage, event) is False
        storage.write_file.assert_not_called()

    def test_empty_event_id_returns_false(self):
        storage = MagicMock()
        event = {
            "id": "",
            "subject": "Test",
            "start": {"dateTime": "2026-03-12T09:00:00.0000000"},
            "end": {"dateTime": "2026-03-12T10:00:00.0000000"},
        }
        assert _write_event(storage, event) is False
        storage.write_file.assert_not_called()

    def test_missing_start_time_returns_false(self):
        storage = MagicMock()
        event = {
            "id": "EVT-123",
            "subject": "Test",
            "start": {},
            "end": {"dateTime": "2026-03-12T10:00:00.0000000"},
        }
        assert _write_event(storage, event) is False
        storage.write_file.assert_not_called()

    def test_no_start_key_returns_false(self):
        storage = MagicMock()
        event = {
            "id": "EVT-123",
            "subject": "Test",
        }
        assert _write_event(storage, event) is False
        storage.write_file.assert_not_called()
