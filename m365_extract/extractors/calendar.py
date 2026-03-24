"""Calendar extractor — syncs events via Graph API calendarView.

Uses /me/calendarView with date range filtering for incremental sync.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import structlog

from m365_extract.config import CalendarExtractorConfig
from m365_extract.converters.html_to_md import html_to_markdown
from m365_extract.frontmatter import build_calendar_frontmatter
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

    events = list(client.get_paginated("/me/calendarView", params=params))
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

        if _write_event(storage, event):
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


def _write_event(storage: StorageBackend, event: dict) -> bool:
    """Write a single calendar event to storage. Returns True if written."""
    event_id = event.get("id", "")
    subject = event.get("subject") or "(no subject)"

    start_obj = event.get("start", {})
    end_obj = event.get("end", {})
    start_time = start_obj.get("dateTime", "")
    end_time = end_obj.get("dateTime", "")

    if not event_id or not start_time:
        log.warning("calendar.skipping_invalid", event_id=event_id)
        return False

    # Normalize times — Graph returns fractional seconds without Z for UTC.
    # Parse and re-format to get a clean ISO string with Z suffix.
    start_time = _normalize_graph_datetime(start_time)
    end_time = _normalize_graph_datetime(end_time)

    # Extract location
    location = event.get("location", {}).get("displayName", "")

    # Extract organizer
    organizer_obj = event.get("organizer", {}).get("emailAddress", {})
    organizer_name = organizer_obj.get("name", "")
    organizer_email = organizer_obj.get("address", "")

    # Extract attendees
    attendees = []
    for att in event.get("attendees", []):
        email_obj = att.get("emailAddress", {})
        att_name = email_obj.get("name", "")
        if att_name:
            attendees.append(att_name)

    # Convert body
    body_obj = event.get("body", {})
    content_type = body_obj.get("contentType", "text")
    raw_body = body_obj.get("content", "")

    if content_type == "html":
        body_md = html_to_markdown(raw_body)
    else:
        body_md = raw_body

    # Build frontmatter
    fm = build_calendar_frontmatter(
        subject=subject,
        event_id=event_id,
        start_time=start_time,
        end_time=end_time,
        location=location,
        organizer_name=organizer_name,
        organizer_email=organizer_email,
        attendees=attendees,
        is_recurring=event.get("type", "singleInstance") != "singleInstance",
        web_link=event.get("webLink", ""),
    )

    # Build body
    body_parts = [f"# {subject}\n"]
    body_parts.append(f"**When:** {start_time} — {end_time}")
    if location:
        body_parts.append(f"**Where:** {location}")
    if organizer_name:
        body_parts.append(f"**Organizer:** {organizer_name}")
    if attendees:
        body_parts.append(f"**Attendees:** {', '.join(attendees)}")
    body_parts.append("")
    body_parts.append("---\n")
    if body_md:
        body_parts.append(body_md)

    content = dumps_markdown(fm, "\n".join(body_parts))

    # File path: calendar/{year}/{month}/{slug}.md
    date_str = start_time[:10]
    year = date_str[:4]
    month = date_str[:7]
    slug = slugify(subject)
    hsh = short_hash(event_id)
    file_path = f"calendar/{year}/{month}/{date_str}-{slug}-{hsh}.md"

    storage.write_file(file_path, content)
    return True
