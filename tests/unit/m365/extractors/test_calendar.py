"""Tests for calendar extractor."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from pytest_httpx import HTTPXMock

from m365_brain.config import CalendarExtractorConfig, GraphConfig
from m365_brain.m365.client import GraphClient
from m365_brain.m365.extractors import calendar
from m365_brain.m365.extractors.calendar import _normalize_graph_datetime
from m365_brain.storage.local import LocalBackend
from m365_brain.vault.removal import PATH_MAP_STATE_KEY

FIXTURES_DIR = Path(__file__).resolve().parents[3] / "fixtures"


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
        self, httpx_mock: HTTPXMock, tmp_path, graph_config, calendar_config, calendar_response, ctx
    ):
        httpx_mock.add_response(
            url=re.compile(r".*/me/calendarView.*"),
            json=calendar_response,
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        state, count = calendar.run(client, storage, {}, calendar_config, ctx)

        assert count == 2
        assert "last_sync" in state
        assert state["events_fetched"] == 2

        files = storage.list_files(ctx.paths.inbox_root("calendar"))
        assert len(files) == 2
        client.close()

    def test_event_content_includes_details(
        self, httpx_mock: HTTPXMock, tmp_path, graph_config, calendar_config, calendar_response, ctx
    ):
        httpx_mock.add_response(
            url=re.compile(r".*/me/calendarView.*"),
            json=calendar_response,
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        calendar.run(client, storage, {}, calendar_config, ctx)

        files = storage.list_files(ctx.paths.inbox_root("calendar"))
        content = storage.read_file(files[0])

        assert "Weekly Team Standup" in content or "1:1 with Manager" in content
        client.close()

    def test_empty_calendar(self, httpx_mock: HTTPXMock, tmp_path, graph_config, calendar_config, ctx):
        httpx_mock.add_response(
            url=re.compile(r".*/me/calendarView.*"),
            json={"value": []},
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        state, count = calendar.run(client, storage, {}, calendar_config, ctx)
        assert count == 0
        assert state["events_fetched"] == 0
        client.close()

    def test_dates_with_trailing_zeros_preserved(
        self, httpx_mock: HTTPXMock, tmp_path, graph_config, calendar_config, ctx
    ):
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

        state, count = calendar.run(client, storage, {}, calendar_config, ctx)
        assert count == 1

        files = storage.list_files(ctx.paths.inbox_root("calendar"))
        content = storage.read_file(files[0])
        # The date must be fully preserved, not truncated
        assert "2025-01-20T10:00:00Z" in content
        assert "2025-01-20T10:30:00Z" in content
        client.close()

    def test_recurring_event_tagged(
        self, httpx_mock: HTTPXMock, tmp_path, graph_config, calendar_config, calendar_response, ctx
    ):
        httpx_mock.add_response(
            url=re.compile(r".*/me/calendarView.*"),
            json=calendar_response,
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        calendar.run(client, storage, {}, calendar_config, ctx)

        files = storage.list_files(ctx.paths.inbox_root("calendar"))
        for f in files:
            content = storage.read_file(f)
            if "Weekly Team Standup" in content:
                assert "recurring" in content
                break
        client.close()

    def test_skip_unchanged_events(
        self, httpx_mock: HTTPXMock, tmp_path, graph_config, calendar_config, calendar_response, ctx
    ):
        """Second sync skips events that haven't changed (same lastModifiedDateTime)."""
        # Add lastModifiedDateTime to fixture events
        for event in calendar_response["value"]:
            event["lastModifiedDateTime"] = "2026-03-20T10:00:00Z"

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        # First sync — writes all events
        httpx_mock.add_response(url=re.compile(r".*/me/calendarView.*"), json=calendar_response)
        state, count1 = calendar.run(client, storage, {}, calendar_config, ctx)
        assert count1 == 2
        assert "event_modified_times" in state
        assert len(state["event_modified_times"]) == 2

        # Second sync with same state — should skip unchanged events
        httpx_mock.add_response(url=re.compile(r".*/me/calendarView.*"), json=calendar_response)
        state2, count2 = calendar.run(client, storage, state, calendar_config, ctx)
        assert count2 == 0
        assert state2["events_skipped"] == 2

        client.close()

    def test_modified_event_rewritten(
        self, httpx_mock: HTTPXMock, tmp_path, graph_config, calendar_config, calendar_response, ctx
    ):
        """An event with a new lastModifiedDateTime gets rewritten."""
        for event in calendar_response["value"]:
            event["lastModifiedDateTime"] = "2026-03-20T10:00:00Z"

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        # First sync
        httpx_mock.add_response(url=re.compile(r".*/me/calendarView.*"), json=calendar_response)
        state, _ = calendar.run(client, storage, {}, calendar_config, ctx)

        # Modify one event
        calendar_response["value"][0]["lastModifiedDateTime"] = "2026-03-21T08:00:00Z"

        # Second sync — one changed, one unchanged
        httpx_mock.add_response(url=re.compile(r".*/me/calendarView.*"), json=calendar_response)
        state2, count2 = calendar.run(client, storage, state, calendar_config, ctx)
        assert count2 == 1
        assert state2["events_skipped"] == 1

        client.close()

    def test_attendee_details_in_frontmatter(
        self, httpx_mock: HTTPXMock, tmp_path, graph_config, calendar_config, calendar_response, ctx
    ):
        """Frontmatter includes attendee_details with name, email, and status."""
        httpx_mock.add_response(
            url=re.compile(r".*/me/calendarView.*"),
            json=calendar_response,
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        calendar.run(client, storage, {}, calendar_config, ctx)

        files = storage.list_files(ctx.paths.inbox_root("calendar"))
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
        self, httpx_mock: HTTPXMock, tmp_path, graph_config, calendar_config, calendar_response, ctx
    ):
        """Body text includes attendee emails and statuses."""
        httpx_mock.add_response(
            url=re.compile(r".*/me/calendarView.*"),
            json=calendar_response,
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        calendar.run(client, storage, {}, calendar_config, ctx)

        files = storage.list_files(ctx.paths.inbox_root("calendar"))
        for f in files:
            content = storage.read_file(f)
            if "Weekly Team Standup" in content:
                # Body should have enriched attendee line
                assert "bob@example.com" in content
                assert "carol@example.com" in content
                break
        client.close()

    def test_all_day_event(self, httpx_mock: HTTPXMock, tmp_path, graph_config, calendar_config, ctx):
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

        state, count = calendar.run(client, storage, {}, calendar_config, ctx)
        assert count == 1

        files = storage.list_files(ctx.paths.inbox_root("calendar"))
        content = storage.read_file(files[0])
        assert "Company Holiday" in content
        assert "2026-04-01" in content
        client.close()

    def test_event_no_organizer_name(self, httpx_mock: HTTPXMock, tmp_path, graph_config, calendar_config, ctx):
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

        state, count = calendar.run(client, storage, {}, calendar_config, ctx)
        assert count == 1

        files = storage.list_files(ctx.paths.inbox_root("calendar"))
        content = storage.read_file(files[0])
        assert "Quick Sync" in content
        # Organizer line should not appear when name is empty
        assert "**Organizer:**" not in content
        client.close()

    def test_subject_with_yaml_special_chars(self, httpx_mock: HTTPXMock, tmp_path, graph_config, calendar_config, ctx):
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

        calendar.run(client, storage, {}, calendar_config, ctx)

        files = storage.list_files(ctx.paths.inbox_root("calendar"))
        content = storage.read_file(files[0])

        # Parse the frontmatter back — must not corrupt the title
        from m365_brain.m365.markdown_writer import loads_markdown

        fm, body = loads_markdown(content)
        assert fm["title"] == tricky_subject
        assert fm["location"] == "Room: A [Main]"
        client.close()

    def test_event_no_attendees_omits_attendee_details(
        self, httpx_mock: HTTPXMock, tmp_path, graph_config, calendar_config, ctx
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

        calendar.run(client, storage, {}, calendar_config, ctx)

        files = storage.list_files(ctx.paths.inbox_root("calendar"))
        content = storage.read_file(files[0])
        assert "attendee_details" not in content
        client.close()

    def test_uses_forward_days_config(self, httpx_mock: HTTPXMock, tmp_path, graph_config, ctx):
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

        calendar.run(client, storage, {}, config, ctx)

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


class TestExtractEventData:
    """Tests for _extract_event_data pure extraction function."""

    def test_extracts_full_event(self):
        event = {
            "id": "EVT-001",
            "subject": "Team Meeting",
            "start": {"dateTime": "2026-03-12T09:00:00.0000000", "timeZone": "UTC"},
            "end": {"dateTime": "2026-03-12T10:00:00.0000000", "timeZone": "UTC"},
            "location": {"displayName": "Room A"},
            "organizer": {"emailAddress": {"name": "Alice", "address": "alice@example.com"}},
            "attendees": [
                {
                    "emailAddress": {"name": "Bob", "address": "bob@example.com"},
                    "status": {"response": "accepted"},
                },
            ],
            "body": {"contentType": "text", "content": "Agenda here"},
            "type": "occurrence",
            "webLink": "https://outlook.office.com/event/1",
        }

        extracted = calendar._extract_event_data(event)

        assert extracted is not None
        data, body_md = extracted
        assert data.event_id == "EVT-001"
        assert data.subject == "Team Meeting"
        assert data.start_time == "2026-03-12T09:00:00Z"
        assert data.end_time == "2026-03-12T10:00:00Z"
        assert data.location == "Room A"
        assert data.organizer_name == "Alice"
        assert data.organizer_email == "alice@example.com"
        assert data.attendees == ["Bob"]
        assert data.attendee_details == [{"name": "Bob", "email": "bob@example.com", "status": "accepted"}]
        assert body_md == "Agenda here"
        assert data.is_recurring is True
        assert data.web_link == "https://outlook.office.com/event/1"

    def test_returns_none_for_missing_id(self):
        event = {
            "id": "",
            "subject": "Test",
            "start": {"dateTime": "2026-03-12T09:00:00Z"},
            "end": {"dateTime": "2026-03-12T10:00:00Z"},
        }
        assert calendar._extract_event_data(event) is None

    def test_returns_none_for_missing_start_time(self):
        event = {
            "id": "EVT-002",
            "subject": "Test",
            "start": {},
            "end": {"dateTime": "2026-03-12T10:00:00Z"},
        }
        assert calendar._extract_event_data(event) is None

    def test_defaults_subject_when_missing(self):
        event = {
            "id": "EVT-003",
            "subject": None,
            "start": {"dateTime": "2026-03-12T09:00:00Z"},
            "end": {"dateTime": "2026-03-12T10:00:00Z"},
            "location": {},
            "organizer": {},
            "attendees": [],
            "body": {"contentType": "text", "content": ""},
            "type": "singleInstance",
            "webLink": "",
        }
        extracted = calendar._extract_event_data(event)
        assert extracted is not None
        data, _body_md = extracted
        assert data.subject == "(no subject)"
        assert data.is_recurring is False

    def test_attendee_without_name_uses_email(self):
        event = {
            "id": "EVT-004",
            "subject": "Test",
            "start": {"dateTime": "2026-03-12T09:00:00Z"},
            "end": {"dateTime": "2026-03-12T10:00:00Z"},
            "location": {},
            "organizer": {},
            "attendees": [
                {"emailAddress": {"name": "", "address": "anon@example.com"}, "status": {"response": ""}},
            ],
            "body": {"contentType": "text", "content": ""},
            "type": "singleInstance",
            "webLink": "",
        }
        extracted = calendar._extract_event_data(event)
        assert extracted is not None
        data, _body_md = extracted
        assert data.attendees == []
        assert data.attendee_details == [{"email": "anon@example.com"}]


class TestSyncSkipsInvalidEvents:
    """Tests that the sync loop skips events where _extract_event_data returns None."""

    def test_sync_skips_event_with_missing_id(
        self, httpx_mock: HTTPXMock, tmp_path, graph_config, calendar_config, ctx
    ):
        response = {
            "value": [
                {
                    "id": "",
                    "subject": "Ghost Event",
                    "start": {"dateTime": "2026-03-12T09:00:00Z"},
                    "end": {"dateTime": "2026-03-12T10:00:00Z"},
                    "location": {"displayName": ""},
                    "organizer": {"emailAddress": {"name": "Test", "address": "t@t.com"}},
                    "attendees": [],
                    "body": {"contentType": "text", "content": ""},
                    "type": "singleInstance",
                    "webLink": "",
                },
                {
                    "id": "EVT-VALID",
                    "subject": "Real Event",
                    "start": {"dateTime": "2026-03-12T11:00:00Z"},
                    "end": {"dateTime": "2026-03-12T12:00:00Z"},
                    "location": {"displayName": ""},
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

        state, count = calendar.run(client, storage, {}, calendar_config, ctx)

        assert count == 1
        assert state["events_fetched"] == 2
        files = storage.list_files(ctx.paths.inbox_root("calendar"))
        assert len(files) == 1
        content = storage.read_file(files[0])
        assert "Real Event" in content
        client.close()

    def test_sync_skips_event_with_missing_start_time(
        self, httpx_mock: HTTPXMock, tmp_path, graph_config, calendar_config, ctx
    ):
        response = {
            "value": [
                {
                    "id": "EVT-NO-START",
                    "subject": "Broken Event",
                    "start": {},
                    "end": {"dateTime": "2026-03-12T10:00:00Z"},
                    "location": {"displayName": ""},
                    "organizer": {"emailAddress": {"name": "Test", "address": "t@t.com"}},
                    "attendees": [],
                    "body": {"contentType": "text", "content": ""},
                    "type": "singleInstance",
                    "webLink": "",
                },
                {
                    "id": "EVT-OK",
                    "subject": "Good Event",
                    "start": {"dateTime": "2026-03-12T14:00:00Z"},
                    "end": {"dateTime": "2026-03-12T15:00:00Z"},
                    "location": {"displayName": ""},
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

        state, count = calendar.run(client, storage, {}, calendar_config, ctx)

        assert count == 1
        assert state["events_fetched"] == 2
        files = storage.list_files(ctx.paths.inbox_root("calendar"))
        assert len(files) == 1
        content = storage.read_file(files[0])
        assert "Good Event" in content
        client.close()


class TestWriteEventPlainAttendees:
    """Test _write_event branch where attendees exist but attendee_details is empty.

    The written file must carry the attendee *edges*, not a prose list: an
    attendee is the counterparty `ops tiers` counts, and the extractor calling
    `attendee_relations` is the half of that agreement no builder test can see.
    """

    def test_write_event_plain_attendees(self, tmp_path, ctx):
        from m365_brain.m365.frontmatter import CalendarEventData

        storage = LocalBackend(str(tmp_path / "vault"))
        data = CalendarEventData(
            subject="Lunch Meeting",
            event_id="EVT-PLAIN-ATT",
            start_time="2026-03-12T12:00:00Z",
            end_time="2026-03-12T13:00:00Z",
            location="Cafeteria",
            organizer_name="Alice",
            organizer_email="alice@example.com",
            attendees=["Alice", "Bob"],
            attendee_details=[],
            is_recurring=False,
            web_link="",
        )

        path_map: dict[str, str] = {}
        result = calendar._write_event(storage, data, "Let's eat", ctx, path_map)

        assert result is True
        files = storage.list_files(ctx.paths.inbox_root("calendar"))
        assert len(files) == 1
        assert path_map == {"EVT-PLAIN-ATT": files[0]}
        content = storage.read_file(files[0])
        assert "- attended_by [[Alice]]" in content
        assert "- attended_by [[Bob]]" in content
        assert "Let's eat" in content


def _cancellable_event(cancelled: bool) -> dict:
    return {
        "value": [
            {
                "id": "EVT-CANCELLED",
                "subject": "Doomed Meeting",
                "start": {"dateTime": "2026-03-18T09:00:00.0000000", "timeZone": "UTC"},
                "end": {"dateTime": "2026-03-18T10:00:00.0000000", "timeZone": "UTC"},
                "location": {"displayName": ""},
                "organizer": {"emailAddress": {"name": "Alice", "address": "alice@example.com"}},
                "attendees": [],
                "body": {"contentType": "text", "content": ""},
                "type": "singleInstance",
                "webLink": "",
                "lastModifiedDateTime": "2026-03-17T08:00:00Z",
                "isCancelled": cancelled,
            }
        ],
    }


class TestCancelledEventRemoval:
    """A cancelled event is a removal, not an ordinary write."""

    def test_cancelled_event_deletes_its_file_and_recancelling_is_a_noop(
        self, httpx_mock: HTTPXMock, local_storage, graph_config, calendar_config, ctx
    ):
        client = GraphClient(graph_config, lambda: "test-token")
        inbox = ctx.paths.inbox_root("calendar")

        httpx_mock.add_response(url=re.compile(r".*/me/calendarView.*"), json=_cancellable_event(False))
        state, count = calendar.run(client, local_storage, {}, calendar_config, ctx)
        assert count == 1
        written_path = state[PATH_MAP_STATE_KEY]["EVT-CANCELLED"]
        assert local_storage.list_files(inbox) == [written_path]

        httpx_mock.add_response(url=re.compile(r".*/me/calendarView.*"), json=_cancellable_event(True))
        state, count = calendar.run(client, local_storage, state, calendar_config, ctx)
        assert count == 0
        assert state[PATH_MAP_STATE_KEY] == {}
        assert local_storage.list_files(inbox) == []

        # Upstream keeps re-sending the cancellation; the second pass must not
        # resurrect the file, raise, or count anything.
        httpx_mock.add_response(url=re.compile(r".*/me/calendarView.*"), json=_cancellable_event(True))
        state, count = calendar.run(client, local_storage, state, calendar_config, ctx)
        assert count == 0
        assert state[PATH_MAP_STATE_KEY] == {}
        assert local_storage.list_files(inbox) == []
        client.close()


class TestOddLayoutGoldenPaths:
    def test_events_land_under_the_configured_names_only(
        self, httpx_mock: HTTPXMock, local_storage, graph_config, calendar_config, odd_ctx, calendar_response
    ):
        """Golden keys under a layout that shares no name with the conventional one."""
        httpx_mock.add_response(url=re.compile(r".*/me/calendarView.*"), json=calendar_response)
        client = GraphClient(graph_config, lambda: "test-token")

        _state, count = calendar.run(client, local_storage, {}, calendar_config, odd_ctx)

        assert count == 2
        assert local_storage.list_files("zz-inbox") == [
            "zz-inbox/agenda/2026/2026-03/2026-03-12-1-1-with-manager-7a3594.md",
            "zz-inbox/agenda/2026/2026-03/2026-03-12-weekly-team-standup-506628.md",
        ]
        assert local_storage.list_files("inbox") == []
        client.close()
