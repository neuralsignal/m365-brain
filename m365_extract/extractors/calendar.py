"""Calendar extractor — syncs events via Graph API calendarView.

Uses /me/calendarView with date range filtering for incremental sync.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import structlog

from m365_extract.config import CalendarExtractorConfig
from m365_extract.converters.html_to_md import html_to_markdown
from m365_extract.frontmatter import CalendarEventData, build_calendar_frontmatter
from m365_extract.graph_client import GraphClient
from m365_extract.markdown_writer import dumps_markdown, short_hash, slugify
from m365_extract.storage.base import StorageBackend

log = structlog.get_logger()

name = "calendar"
required_scopes = ["Calendars.Read"]


def run(
    client: GraphClient,
    storage: StorageBackend,
    state: dict,
    config: CalendarExtractorConfig,
) -> tuple[dict, int]:
    """Extract calendar events using calendarView.

    Returns (updated_state, items_written).
    """
    now = datetime.now(UTC)
    lookback = timedelta(days=config.lookback_days)
    start = now - lookback
    end = now + timedelta(days=config.forward_days)

    params = {
        "startDateTime": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "endDateTime": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "$select": "id,subject,start,end,location,organizer,attendees,body,type,webLink,lastModifiedDateTime",
        "$top": "50",
        "$orderby": "start/dateTime",
    }

    events = list(client.get_paginated("/me/calendarView", params=params, max_pages=client.max_pages))
    log.info("calendar.fetched", count=len(events))

    # Skip-unchanged: track event_id → lastModifiedDateTime
    known_modified = state.get("event_modified_times", {})
    event_modified: dict[str, str] = {}

    written = 0
    skipped = 0
    for event in events:
        event_id = event.get("id", "")
        last_modified = event.get("lastModifiedDateTime", "")

        # Track all events we see
        if event_id and last_modified:
            event_modified[event_id] = last_modified

        # Skip write if unchanged since last sync
        if event_id and last_modified and known_modified.get(event_id) == last_modified:
            skipped += 1
            continue

        event_data = _extract_event_data(event)
        if event_data and _write_event(storage, event_data):
            written += 1

    state["last_sync"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    state["events_fetched"] = len(events)
    state["events_written"] = written
    state["events_skipped"] = skipped
    state["event_modified_times"] = event_modified
    log.info("calendar.sync_complete", written=written, skipped=skipped, total=len(events))
    return state, written


def _normalize_graph_datetime(dt_str: str) -> str:
    """Normalize a Graph API datetime string to ISO 8601 with Z suffix.

    Graph returns datetimes like '2026-03-12T09:00:00.0000000' (no Z, fractional seconds).
    """
    if not dt_str:
        return dt_str
    # Strip trailing Z if present so we can parse uniformly
    clean = dt_str.rstrip("Z")
    try:
        parsed = datetime.fromisoformat(clean)
    except ValueError:
        return dt_str  # Return as-is if unparseable
    return parsed.strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass(frozen=True)
class EventData:
    """Extracted and normalized calendar event fields."""

    event_id: str
    subject: str
    start_time: str
    end_time: str
    location: str
    organizer_name: str
    organizer_email: str
    attendees: list[str]
    attendee_details: list[dict[str, str]]
    body_md: str
    is_recurring: bool
    web_link: str


def _extract_event_data(event: dict) -> EventData | None:
    """Extract and normalize calendar event data. Returns None if invalid."""
    event_id = event.get("id", "")
    subject = event.get("subject") or "(no subject)"

    start_obj = event.get("start", {})
    end_obj = event.get("end", {})
    start_time = start_obj.get("dateTime", "")
    end_time = end_obj.get("dateTime", "")

    if not event_id or not start_time:
        log.warning("calendar.skipping_invalid", event_id=event_id)
        return None

    start_time = _normalize_graph_datetime(start_time)
    end_time = _normalize_graph_datetime(end_time)

    location = event.get("location", {}).get("displayName", "")

    organizer_obj = event.get("organizer", {}).get("emailAddress", {})
    organizer_name = organizer_obj.get("name", "")
    organizer_email = organizer_obj.get("address", "")

    attendees: list[str] = []
    attendee_details: list[dict[str, str]] = []
    for att in event.get("attendees", []):
        email_obj = att.get("emailAddress", {})
        att_name = email_obj.get("name", "")
        att_email = email_obj.get("address", "")
        att_status = att.get("status", {}).get("response", "")
        if att_name:
            attendees.append(att_name)
        if att_name or att_email:
            detail: dict[str, str] = {}
            if att_name:
                detail["name"] = att_name
            if att_email:
                detail["email"] = att_email
            if att_status:
                detail["status"] = att_status
            attendee_details.append(detail)

    body_obj = event.get("body", {})
    content_type = body_obj.get("contentType", "text")
    raw_body = body_obj.get("content", "")
    body_md = html_to_markdown(raw_body) if content_type == "html" else raw_body

    return EventData(
        event_id=event_id,
        subject=subject,
        start_time=start_time,
        end_time=end_time,
        location=location,
        organizer_name=organizer_name,
        organizer_email=organizer_email,
        attendees=attendees,
        attendee_details=attendee_details,
        body_md=body_md,
        is_recurring=event.get("type", "singleInstance") != "singleInstance",
        web_link=event.get("webLink", ""),
    )


def _write_event(storage: StorageBackend, data: EventData) -> bool:
    """Build frontmatter and markdown body for a calendar event, then write to storage."""
    fm = build_calendar_frontmatter(
        CalendarEventData(
            subject=data.subject,
            event_id=data.event_id,
            start_time=data.start_time,
            end_time=data.end_time,
            location=data.location,
            organizer_name=data.organizer_name,
            organizer_email=data.organizer_email,
            attendees=data.attendees,
            attendee_details=data.attendee_details,
            is_recurring=data.is_recurring,
            web_link=data.web_link,
        )
    )

    body_parts = [f"# {data.subject}\n"]
    body_parts.append(f"**When:** {data.start_time} — {data.end_time}")
    if data.location:
        body_parts.append(f"**Where:** {data.location}")
    if data.organizer_name:
        body_parts.append(f"**Organizer:** {data.organizer_name}")
    if data.attendee_details:
        att_strs = []
        for d in data.attendee_details:
            parts = [d.get("name", "")]
            extra = []
            if d.get("email"):
                extra.append(d["email"])
            if d.get("status"):
                extra.append(d["status"])
            if extra:
                parts.append(f"({', '.join(extra)})")
            att_strs.append(" ".join(parts))
        body_parts.append(f"**Attendees:** {', '.join(att_strs)}")
    elif data.attendees:
        body_parts.append(f"**Attendees:** {', '.join(data.attendees)}")
    body_parts.append("")
    body_parts.append("---\n")
    if data.body_md:
        body_parts.append(data.body_md)

    content = dumps_markdown(fm, "\n".join(body_parts))

    date_str = data.start_time[:10]
    year = date_str[:4]
    month = date_str[:7]
    slug = slugify(data.subject, 80)
    hsh = short_hash(data.event_id, 6)
    file_path = f"calendar/{year}/{month}/{date_str}-{slug}-{hsh}.md"

    storage.write_file(file_path, content)
    return True
