"""`source` is one shape, whatever the entity is -- and one vocabulary.

Four of the eight builders used to omit `source.url` entirely, so a consumer
doing `fm["source"]["url"]` worked on an email and raised `KeyError` on a
contact. The absent key was the bug; `None` is the answer for an entity with no
upstream web link. This test is the thing that stops the eight drifting apart
again -- a new builder that forgets a key fails here, not in a consumer.

The two guards below the shape tests are the other half: a builder may not
write a key whose *name* the consuming corpus already owns (`status`), and a
name it shares with the file catalog may not carry a different kind of value
(`source` was a provenance dict here and an extractor name there).
"""

from __future__ import annotations

import dataclasses

import pytest

from m365_brain.m365.frontmatter import (
    CalendarEventData,
    ContactData,
    DirectoryUserData,
    EmailData,
    OneDriveFileData,
    SharePointFileData,
    TeamsChannelData,
    TeamsChatData,
    build_calendar_frontmatter,
    build_contact_frontmatter,
    build_directory_user_frontmatter,
    build_email_frontmatter,
    build_onedrive_frontmatter,
    build_sharepoint_frontmatter,
    build_teams_channel_frontmatter,
    build_teams_chat_frontmatter,
)
from m365_brain.model import CatalogEntry

SOURCE_KEYS = {"system", "service", "id", "url", "extracted_at", "extractor"}

# One minimal instance per builder. Every builder in `frontmatter.__all__` must
# appear here; the completeness test below is what enforces that.
BUILDERS = {
    "calendar": (
        build_calendar_frontmatter,
        CalendarEventData(
            subject="Standup",
            event_id="ev-1",
            start_time="2026-03-12T10:00:00Z",
            end_time="2026-03-12T10:15:00Z",
            location="",
            organizer_name="Alice",
            organizer_email="alice@example.com",
            attendees=[],
            attendee_details=[],
            is_recurring=False,
            web_link="https://outlook.office.com/e/1",
        ),
    ),
    "email": (
        build_email_frontmatter,
        EmailData(
            subject="Invoice",
            message_id="msg-1",
            conversation_id="conv-1",
            received_time="2026-03-12T10:00:00Z",
            folder="Inbox",
            mailbox="me@example.com",
            sender_address="alice@example.com",
            sender_name="Alice",
            to_recipients=[],
            importance="normal",
            has_attachments=False,
            web_link="https://outlook.office365.com/m/1",
        ),
    ),
    "onedrive": (
        build_onedrive_frontmatter,
        OneDriveFileData(
            file_name="report.docx",
            item_id="item-1",
            size=1024,
            modified_time="2026-03-12T10:00:00Z",
            modified_by="Alice",
            parent_path="Documents",
            web_url="https://example-my.sharepoint.com/report.docx",
            content_status="pending",
        ),
    ),
    "sharepoint": (
        build_sharepoint_frontmatter,
        SharePointFileData(
            file_name="plan.pptx",
            item_id="item-2",
            size=2048,
            modified_time="2026-03-12T10:00:00Z",
            modified_by="Bob",
            parent_path="Shared/Plans",
            web_url="https://example.sharepoint.com/plan.pptx",
            site_name="Engineering",
            drive_name="Documents",
            content_status="pending",
        ),
    ),
    "contact": (
        build_contact_frontmatter,
        ContactData(
            display_name="Jane Smith",
            contact_id="c-1",
            email_addresses=[],
            phones=[],
            company="",
            job_title="",
            department="",
            categories=[],
        ),
    ),
    "directory_user": (
        build_directory_user_frontmatter,
        DirectoryUserData(
            display_name="Dana Lee",
            user_id="u-1",
            email="dana@example.com",
            upn="dana@example.com",
            job_title="",
            department="",
            office="",
            city="",
            manager_link="",
            direct_reports_links=[],
        ),
    ),
    "teams_chat": (
        build_teams_chat_frontmatter,
        TeamsChatData(
            title="Alice Smith",
            conversation_id="chat-1",
            conversation_type="oneOnOne",
            participants=[],
            last_message_time="2026-03-12T10:00:00Z",
            message_count=0,
            history_complete=True,
        ),
    ),
    "teams_channel": (
        build_teams_channel_frontmatter,
        TeamsChannelData(
            team_name="Engineering",
            channel_name="General",
            channel_id="ch-1",
            last_message_time="2026-03-12T10:00:00Z",
            message_count=0,
            history_complete=True,
        ),
    ),
}


@pytest.mark.parametrize("entity", sorted(BUILDERS), ids=sorted(BUILDERS))
def test_source_carries_the_same_keys_for_every_entity_type(entity: str) -> None:
    build, data = BUILDERS[entity]

    assert set(build(data)["source"]) == SOURCE_KEYS


@pytest.mark.parametrize("entity", sorted(BUILDERS), ids=sorted(BUILDERS))
def test_source_url_is_always_addressable_present_or_none(entity: str) -> None:
    """`fm["source"]["url"]` must never raise -- that was the whole defect."""
    build, data = BUILDERS[entity]
    url = build(data)["source"]["url"]

    assert url is None or isinstance(url, str)


CONSUMER_OWNED_KEYS = {"status"}
"""Frontmatter keys whose vocabulary belongs to the consuming corpus, not here.

`status` is the one: a vault's own notes, goals and tasks carry an authored
lifecycle under it (`completed`, `in_progress`, `active`, `pending`), and
`observation.category` is a single flat namespace, so a builder writing
`status: raw` puts a write-only constant into the same bucket the consumer's
agents query. Measured on a live index before the removal: 24,217 of 24,285
rows under `status` were that constant, and nothing in this package ever read
one back.
"""


@pytest.mark.parametrize("entity", sorted(BUILDERS), ids=sorted(BUILDERS))
def test_no_builder_writes_a_key_the_consuming_corpus_owns(entity: str) -> None:
    """A builder may not spend a word the vault it writes into already spends."""
    build, data = BUILDERS[entity]

    assert set(build(data)) & CONSUMER_OWNED_KEYS == set()


CATALOG_FIELD_TYPES = {field.name: field.type for field in dataclasses.fields(CatalogEntry)}
"""`CatalogEntry`'s own field names and annotations, read off the shipped dataclass."""


@pytest.mark.parametrize("entity", sorted(BUILDERS), ids=sorted(BUILDERS))
def test_a_name_shared_with_the_catalog_carries_the_same_kind_of_value(entity: str) -> None:
    """Sharing a name with a catalog column is fine; sharing it over two value sets is not.

    `file_name` means a file's name on both sides -- one concept stated at two
    layers, which is not a collision. `source` did not: a dict of upstream
    provenance here, a bare extractor name there, so `--source exchange`
    (the `service` an operator had just read in frontmatter) matched nothing
    while the answer was `email`. The catalog column is now `extractor`, which
    is what its whole value set always was.
    """
    build, data = BUILDERS[entity]
    scalar = (str, int, bool)

    for key, value in build(data).items():
        annotation = CATALOG_FIELD_TYPES.get(key)
        if annotation is None:
            continue
        assert isinstance(value, scalar), (
            f"frontmatter key {key!r} is a {type(value).__name__}, but CatalogEntry.{key} is "
            f"{annotation!r} -- one name over two value sets. Rename whichever side is narrower."
        )


def test_every_exported_builder_is_covered() -> None:
    """A new builder has to be added above, or it drifts unnoticed."""
    from m365_brain.m365 import frontmatter

    exported = {name for name in frontmatter.__all__ if name.startswith("build_")}
    covered = {build.__name__ for build, _ in BUILDERS.values()}

    assert covered == exported
