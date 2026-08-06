"""Calendar frontmatter builder, and the one fact that cannot live in frontmatter.

An event's counterparties are its attendees, and there are N of them. A
frontmatter key holds one value, and `m365_brain/parsers/document.py` promotes
only a **scalar** key to an observation -- a list stays in metadata, which no
per-entity read can reach. So `attendees: [...]` renders for a human and is
invisible to `ops tiers`, which is what made that verb report an empty corpus.

Joining the names into one string is the wrong repair here even though it was
the right one for an email's `to` line: `ops tiers` groups on the whole
observation, so a joined value becomes a single counterparty called
"Ana Ruiz, Bo Frey". The shape that carries N readable counterparties is a body
relation, one line per attendee -- `attendee_relations` below -- which is what
`ops.tiers.interaction_sources[].party_from.relation` names.
"""

from __future__ import annotations

from dataclasses import dataclass

from m365_brain.m365.markdown_writer import now_iso, short_hash, slugify

ATTENDED_BY = "attended_by"
"""The relation type each attendee edge is written under.

This extractor's vocabulary, like every frontmatter key in this module, so it
is a literal rather than config -- and a named one, because the config that
reads it (`ops.tiers.interaction_sources`) has to spell the same word and a
grep for it should find both ends.
"""


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
            "extractor": "m365-brain/calendar/1.1",
        },
    }
    if data.location:
        fm["location"] = data.location
    if data.attendee_details:
        fm["attendee_details"] = data.attendee_details
    return fm


def attendee_relations(data: CalendarEventData) -> list[str]:
    """One `- attended_by [[Name]] (email, status)` line per attendee.

    Written into the markdown body, because that is the only place an event can
    state N counterparties in a shape the index reads back -- see the module
    docstring. `attendee_details` is preferred over `attendees` when present:
    the two carry the same people, and the detailed form also covers an
    attendee who has an address but no display name.
    """
    details = data.attendee_details or [{"name": name} for name in data.attendees]
    lines: list[str] = []
    for detail in details:
        target = detail.get("name") or detail.get("email") or ""
        if not target:
            continue
        context = [value for key in ("email", "status") if (value := detail.get(key, "")) and value != target]
        suffix = f" ({', '.join(context)})" if context else ""
        lines.append(f"- {ATTENDED_BY} [[{target}]]{suffix}")
    return lines
