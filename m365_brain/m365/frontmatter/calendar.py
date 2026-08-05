"""Calendar frontmatter builder."""

from __future__ import annotations

from dataclasses import dataclass

from m365_brain.m365.markdown_writer import now_iso, short_hash, slugify


@dataclass(frozen=True)
class CalendarEventData:
    subject: str
    event_id: str
    start_time: str
    end_time: str
    location: str
    organizer_name: str
    organizer_email: str
    attendees: list[str]
    attendee_details: list[dict]
    is_recurring: bool
    web_link: str


def build_calendar_frontmatter(data: CalendarEventData) -> dict:
    """Build frontmatter dict for a calendar event."""
    date_str = data.start_time[:10]
    slug = slugify(data.subject, 80)
    permalink = f"calendar-{date_str}-{slug}-{short_hash(data.event_id, 6)}"
    tags = ["calendar"]
    if data.is_recurring:
        tags.append("recurring")
    fm: dict = {
        "title": data.subject,
        "permalink": permalink,
        "type": "calendar_event",
        "tags": tags,
        "start": data.start_time,
        "end": data.end_time,
        "organizer": data.organizer_name,
        "organizer_email": data.organizer_email,
        "attendees": data.attendees,
        "is_recurring": data.is_recurring,
        "source": {
            "system": "microsoft365",
            "service": "exchange",
            "id": data.event_id,
            "url": data.web_link,
            "extracted_at": now_iso(),
            "extractor": "m365-brain/calendar/1.0",
        },
        "status": "raw",
    }
    if data.location:
        fm["location"] = data.location
    if data.attendee_details:
        fm["attendee_details"] = data.attendee_details
    return fm
