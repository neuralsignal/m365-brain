"""Tests for the email frontmatter builder."""

from __future__ import annotations

import re

from hypothesis import given
from hypothesis import strategies as st

from m365_brain.m365.frontmatter.email import EmailData, build_email_frontmatter

EXPECTED_KEYS = {
    "title",
    "permalink",
    "type",
    "tags",
    "sender",
    "sender_name",
    "to",
    "date",
    "folder",
    "mailbox",
    "importance",
    "has_attachments",
    "source",
    "status",
}

EMAILS = st.builds(
    EmailData,
    subject=st.text(min_size=1, max_size=60),
    message_id=st.text(min_size=1, max_size=40),
    received_time=st.sampled_from(["2026-03-12T10:00:00Z", "2025-11-01T08:30:00Z"]),
    folder=st.sampled_from(["Inbox", "Sent Items", "Archive/2026", "Deleted Items"]),
    mailbox=st.emails(),
    sender_address=st.emails(),
    sender_name=st.text(max_size=30),
    to_recipients=st.lists(st.emails(), max_size=5),
    importance=st.sampled_from(["low", "normal", "high"]),
    has_attachments=st.booleans(),
    web_link=st.text(max_size=40),
)


class TestEmailFrontmatterProperties:
    @given(EMAILS)
    def test_key_set_is_fixed(self, data: EmailData):
        """The email builder has no conditional keys — the shape never varies with the input."""
        fm = build_email_frontmatter(data)

        assert set(fm) == EXPECTED_KEYS
        assert fm["type"] == "email"
        assert fm["status"] == "raw"
        assert fm["source"]["extractor"] == "m365-brain/email/1.1"
        assert fm["source"]["service"] == "exchange"
        assert fm["source"]["id"] == data.message_id

    @given(EMAILS)
    def test_permalink_is_slug_safe(self, data: EmailData):
        fm = build_email_frontmatter(data)

        assert re.fullmatch(rf"email-{data.received_time[:10]}-[a-z0-9-]+-[0-9a-f]{{6}}", fm["permalink"])

    @given(EMAILS)
    def test_tags_are_folder_derived_strings(self, data: EmailData):
        fm = build_email_frontmatter(data)

        assert all(isinstance(tag, str) for tag in fm["tags"])
        assert fm["tags"][0] == "email"
        assert len(fm["tags"]) == 2
        assert " " not in fm["tags"][1]
        assert fm["tags"][1] == fm["tags"][1].lower()


class TestEmailFrontmatterShapes:
    def test_folder_with_spaces_becomes_hyphenated_tag(self):
        fm = build_email_frontmatter(
            EmailData(
                subject="Re: Invoice",
                message_id="msg-1",
                received_time="2026-03-12T10:00:00Z",
                folder="Sent Items",
                mailbox="me@example.com",
                sender_address="alice@example.com",
                sender_name="Alice",
                to_recipients=["bob@example.com", "carol@example.com"],
                importance="high",
                has_attachments=True,
                web_link="https://outlook.office365.com/m/1",
            )
        )

        assert fm["tags"] == ["email", "sent-items"]
        assert fm["folder"] == "Sent Items"
        assert fm["to"] == ["bob@example.com", "carol@example.com"]
        assert fm["importance"] == "high"
        assert fm["has_attachments"] is True
        assert fm["source"]["url"] == "https://outlook.office365.com/m/1"

    def test_email_with_no_recipients(self):
        fm = build_email_frontmatter(
            EmailData(
                subject="Draft note to self",
                message_id="msg-2",
                received_time="2026-03-12T10:00:00Z",
                folder="Drafts",
                mailbox="me@example.com",
                sender_address="me@example.com",
                sender_name="",
                to_recipients=[],
                importance="normal",
                has_attachments=False,
                web_link="",
            )
        )

        assert fm["to"] == []
        assert fm["sender_name"] == ""
        assert fm["permalink"].startswith("email-2026-03-12-draft-note-to-self-")

    def test_empty_folder_yields_empty_second_tag(self):
        """A blank folder produces a literal empty tag — the folder value is not guarded."""
        fm = build_email_frontmatter(
            EmailData(
                subject="Orphan",
                message_id="msg-3",
                received_time="2026-03-12T10:00:00Z",
                folder="",
                mailbox="me@example.com",
                sender_address="alice@example.com",
                sender_name="Alice",
                to_recipients=[],
                importance="normal",
                has_attachments=False,
                web_link="",
            )
        )

        assert fm["tags"] == ["email", ""]

    def test_nested_folder_path_keeps_separator_in_tag(self):
        """Only spaces are replaced — a nested folder path keeps its slash in the tag."""
        fm = build_email_frontmatter(
            EmailData(
                subject="Archived thread",
                message_id="msg-4",
                received_time="2026-03-12T10:00:00Z",
                folder="Archive/Old Projects",
                mailbox="me@example.com",
                sender_address="alice@example.com",
                sender_name="Alice",
                to_recipients=["me@example.com"],
                importance="normal",
                has_attachments=False,
                web_link="",
            )
        )

        assert fm["tags"] == ["email", "archive/old-projects"]
