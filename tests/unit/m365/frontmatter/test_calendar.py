"""Tests for the calendar frontmatter builder."""

from __future__ import annotations

import re

from hypothesis import given
from hypothesis import strategies as st

from m365_brain.m365.frontmatter.calendar import CalendarEventData, build_calendar_frontmatter

REQUIRED_KEYS = {
    "title",
    "permalink",
    "type",
    "tags",
    "start",
    "end",
    "organizer",
    "organizer_email",
    "attendees",
    "is_recurring",
    "source",
    "status",
}

ISO_TIMES = st.sampled_from(["2026-03-12T09:00:00Z", "2026-01-01T00:00:00Z", "2025-12-31T23:59:59Z"])

EVENTS = st.builds(
    CalendarEventData,
    subject=st.text(min_size=1, max_size=60),
    event_id=st.text(min_size=1, max_size=30),
    start_time=ISO_TIMES,
    end_time=ISO_TIMES,
    location=st.text(max_size=30),
    organizer_name=st.text(max_size=30),
    organizer_email=st.emails(),
    attendees=st.lists(st.text(min_size=1, max_size=20), max_size=4),
    attendee_details=st.lists(st.fixed_dictionaries({"email": st.emails()}), max_size=4),
    is_recurring=st.booleans(),
    web_link=st.text(max_size=40),
)


class TestCalendarFrontmatterProperties:
    @given(EVENTS)
    def test_required_keys_and_constants(self, data: CalendarEventData):
        fm = build_calendar_frontmatter(data)

        assert set(fm) >= REQUIRED_KEYS
        assert fm["type"] == "calendar_event"
        assert fm["status"] == "raw"
        assert fm["source"]["system"] == "microsoft365"
        assert fm["source"]["service"] == "exchange"
        assert fm["source"]["extractor"] == "m365-brain/calendar/1.0"
        assert fm["source"]["id"] == data.event_id
        assert all(isinstance(tag, str) for tag in fm["tags"])
        # permalink is ascii-safe: calendar-<YYYY-MM-DD>-<slug>-<6 hex>
        assert re.fullmatch(rf"calendar-{data.start_time[:10]}-[a-z0-9-]+-[0-9a-f]{{6}}", fm["permalink"])

    @given(EVENTS)
    def test_recurring_flag_drives_tag(self, data: CalendarEventData):
        fm = build_calendar_frontmatter(data)

        assert fm["tags"][0] == "calendar"
        assert ("recurring" in fm["tags"]) is data.is_recurring
        assert fm["is_recurring"] is data.is_recurring

    @given(EVENTS)
    def test_no_none_values_emitted(self, data: CalendarEventData):
        fm = build_calendar_frontmatter(data)

        assert all(value is not None for value in fm.values())
        assert all(value is not None for value in fm["source"].values())


class TestCalendarFrontmatterShapes:
    def test_event_with_attendees(self):
        fm = build_calendar_frontmatter(
            CalendarEventData(
                subject="Quarterly Review",
                event_id="evt-1",
                start_time="2026-03-12T09:00:00Z",
                end_time="2026-03-12T10:00:00Z",
                location="Room A",
                organizer_name="Alice",
                organizer_email="alice@example.com",
                attendees=["Bob"],
                attendee_details=[{"name": "Bob", "email": "bob@example.com", "status": "accepted"}],
                is_recurring=False,
                web_link="https://outlook.office.com/e/1",
            )
        )

        assert fm["title"] == "Quarterly Review"
        assert fm["start"] == "2026-03-12T09:00:00Z"
        assert fm["end"] == "2026-03-12T10:00:00Z"
        assert fm["organizer"] == "Alice"
        assert fm["organizer_email"] == "alice@example.com"
        assert fm["location"] == "Room A"
        assert fm["attendee_details"] == [{"name": "Bob", "email": "bob@example.com", "status": "accepted"}]
        assert fm["source"]["url"] == "https://outlook.office.com/e/1"
        assert fm["permalink"].startswith("calendar-2026-03-12-quarterly-review-")

    def test_empty_attendees_kept_but_details_and_location_dropped(self):
        """`attendees` is unconditional; `location`/`attendee_details` are omitted when falsy."""
        fm = build_calendar_frontmatter(
            CalendarEventData(
                subject="Focus Time",
                event_id="evt-2",
                start_time="2026-03-12T09:00:00Z",
                end_time="2026-03-12T11:00:00Z",
                location="",
                organizer_name="",
                organizer_email="",
                attendees=[],
                attendee_details=[],
                is_recurring=False,
                web_link="",
            )
        )

        assert fm["attendees"] == []
        assert "attendee_details" not in fm
        assert "location" not in fm
        # empty organizer strings are still emitted
        assert fm["organizer"] == ""
        assert fm["organizer_email"] == ""

    def test_short_start_time_truncates_permalink_date(self):
        """The date segment is a blind `start_time[:10]` slice — a date-only start yields no time part."""
        fm = build_calendar_frontmatter(
            CalendarEventData(
                subject="All Hands",
                event_id="evt-3",
                start_time="2026-04-01",
                end_time="2026-04-02",
                location="",
                organizer_name="Admin",
                organizer_email="admin@example.com",
                attendees=[],
                attendee_details=[],
                is_recurring=True,
                web_link="",
            )
        )

        assert fm["permalink"].startswith("calendar-2026-04-01-all-hands-")
        assert fm["tags"] == ["calendar", "recurring"]

    def test_unicode_subject_slugified_but_title_preserved(self):
        fm = build_calendar_frontmatter(
            CalendarEventData(
                subject="Zürich Café: Q1 (Final)",
                event_id="evt-4",
                start_time="2026-03-12T09:00:00Z",
                end_time="2026-03-12T10:00:00Z",
                location="",
                organizer_name="Alice",
                organizer_email="alice@example.com",
                attendees=[],
                attendee_details=[],
                is_recurring=False,
                web_link="",
            )
        )

        assert fm["title"] == "Zürich Café: Q1 (Final)"
        assert fm["permalink"].startswith("calendar-2026-03-12-zurich-cafe-q1-final-")
