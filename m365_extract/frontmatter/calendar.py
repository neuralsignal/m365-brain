"""Calendar frontmatter builder."""

from __future__ import annotations

from m365_extract.markdown_writer import now_iso, short_hash, slugify


def build_calendar_frontmatter(
    *,
    subject: str,
    event_id: str,
    start_time: str,
    end_time: str,
    location: str,
    organizer_name: str,
    organizer_email: str,
    attendees: list[str],
    attendee_details: list[dict],
    is_recurring: bool,
    web_link: str,
) -> dict:
    """Build frontmatter dict for a calendar event."""
    date_str = start_time[:10]
    slug = slugify(subject, 80)
    permalink = f"calendar-{date_str}-{slug}-{short_hash(event_id, 6)}"
    tags = ["calendar"]
    if is_recurring:
        tags.append("recurring")
    fm: dict = {
        "title": subject,
        "permalink": permalink,
        "type": "calendar_event",
        "tags": tags,
        "start": start_time,
        "end": end_time,
        "organizer": organizer_name,
        "organizer_email": organizer_email,
        "attendees": attendees,
        "is_recurring": is_recurring,
        "source": {
            "system": "microsoft365",
            "service": "exchange",
            "id": event_id,
            "url": web_link,
            "extracted_at": now_iso(),
            "extractor": "m365-extract/calendar/1.0",
        },
        "status": "raw",
    }
    if location:
        fm["location"] = location
    if attendee_details:
        fm["attendee_details"] = attendee_details
    return fm
