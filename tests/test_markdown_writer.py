"""Tests for markdown writer and frontmatter builders."""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from m365_extract.markdown_writer import (
    build_calendar_frontmatter,
    build_contact_frontmatter,
    build_directory_user_frontmatter,
    build_email_frontmatter,
    build_onedrive_frontmatter,
    build_sharepoint_frontmatter,
    build_teams_channel_frontmatter,
    build_teams_chat_frontmatter,
    dumps_markdown,
    loads_markdown,
    short_hash,
    slugify,
)


class TestSlugify:
    def test_simple_text(self):
        assert slugify("Hello World") == "hello-world"

    def test_special_characters(self):
        assert slugify("RE: Q1 Budget (Final)") == "re-q1-budget-final"

    def test_accented_characters(self):
        assert slugify("Zürich Café") == "zurich-cafe"

    def test_empty_string(self):
        assert slugify("") == "untitled"

    def test_max_length(self):
        result = slugify("a" * 200, max_length=10)
        assert len(result) <= 10

    def test_strips_trailing_hyphens_on_truncation(self):
        result = slugify("hello-world-this-is-long", max_length=12)
        assert not result.endswith("-")

    @given(st.text(min_size=1, max_size=200))
    def test_always_returns_valid_slug(self, text):
        result = slugify(text)
        assert len(result) <= 80
        assert result == result.lower()
        assert "--" not in result
        assert not result.startswith("-")
        assert not result.endswith("-")


class TestShortHash:
    def test_deterministic(self):
        assert short_hash("hello") == short_hash("hello")

    def test_different_inputs(self):
        assert short_hash("hello") != short_hash("world")

    def test_length(self):
        assert len(short_hash("test", length=8)) == 8


class TestDumpsLoadsMarkdown:
    def test_round_trip(self):
        metadata = {"title": "Test", "type": "email"}
        body = "# Hello\n\nThis is a test."
        serialized = dumps_markdown(metadata, body)
        loaded_meta, loaded_body = loads_markdown(serialized)
        assert loaded_meta["title"] == "Test"
        assert loaded_meta["type"] == "email"
        assert "Hello" in loaded_body


class TestBuildEmailFrontmatter:
    def test_basic_email(self):
        fm = build_email_frontmatter(
            subject="Test Subject",
            message_id="msg-123",
            received_time="2026-03-12T10:00:00Z",
            folder="Inbox",
            sender_address="alice@example.com",
            sender_name="Alice",
            to_recipients=["bob@example.com"],
            importance="normal",
            has_attachments=False,
            web_link="https://outlook.office365.com/test",
        )
        assert fm["title"] == "Test Subject"
        assert fm["type"] == "email"
        assert "email" in fm["tags"]
        assert fm["sender"] == "alice@example.com"
        assert fm["source"]["extractor"] == "m365-extract/email/1.0"
        assert "permalink" in fm

    def test_permalink_includes_date_and_hash(self):
        fm = build_email_frontmatter(
            subject="Budget Review",
            message_id="msg-456",
            received_time="2026-03-12T10:00:00Z",
            folder="Inbox",
            sender_address="alice@example.com",
            sender_name="Alice",
            to_recipients=[],
            importance="normal",
            has_attachments=False,
            web_link="",
        )
        assert fm["permalink"].startswith("email-2026-03-12-budget-review-")


class TestBuildCalendarFrontmatter:
    def test_basic_event(self):
        fm = build_calendar_frontmatter(
            subject="Team Meeting",
            event_id="evt-123",
            start_time="2026-03-12T09:00:00Z",
            end_time="2026-03-12T10:00:00Z",
            location="Room A",
            organizer_name="Boss",
            organizer_email="boss@example.com",
            attendees=["Alice", "Bob"],
            is_recurring=True,
            web_link="",
        )
        assert fm["type"] == "calendar_event"
        assert "recurring" in fm["tags"]
        assert fm["location"] == "Room A"
        assert fm["attendees"] == ["Alice", "Bob"]

    def test_no_location_omits_field(self):
        fm = build_calendar_frontmatter(
            subject="Call",
            event_id="evt-456",
            start_time="2026-03-12T09:00:00Z",
            end_time="2026-03-12T10:00:00Z",
            location="",
            organizer_name="Boss",
            organizer_email="boss@example.com",
            attendees=[],
            is_recurring=False,
            web_link="",
        )
        assert "location" not in fm


class TestBuildTeamsChatFrontmatter:
    def test_basic_chat(self):
        fm = build_teams_chat_frontmatter(
            title="Alice, Bob",
            conversation_id="chat-123",
            conversation_type="oneOnOne",
            participants=["Alice", "Bob"],
            last_message_time="2026-03-12T10:00:00Z",
        )
        assert fm["type"] == "teams_chat"
        assert "teams-oneonone" in fm["tags"]
        assert fm["participants"] == ["Alice", "Bob"]


class TestBuildOneDriveFrontmatter:
    def test_basic_file(self):
        fm = build_onedrive_frontmatter(
            file_name="report.docx",
            item_id="item-123",
            size=45000,
            modified_time="2026-03-12T10:00:00Z",
            modified_by="Alice Smith",
            parent_path="Documents/Reports",
            web_url="https://example.com/report.docx",
            conversion_status="pending",
        )
        assert fm["type"] == "onedrive_file"
        assert fm["title"] == "report.docx"
        assert "onedrive" in fm["tags"]
        assert "docx" in fm["tags"]
        assert fm["file_size"] == 45000
        assert fm["modified_by"] == "Alice Smith"
        assert fm["parent_path"] == "Documents/Reports"
        assert fm["conversion_status"] == "pending"
        assert fm["source"]["service"] == "onedrive"
        assert fm["source"]["extractor"] == "m365-extract/onedrive/1.0"
        assert "permalink" in fm

    def test_file_without_extension(self):
        fm = build_onedrive_frontmatter(
            file_name="Makefile",
            item_id="item-456",
            size=100,
            modified_time="2026-03-12T10:00:00Z",
            modified_by="Bob",
            parent_path="",
            web_url="",
            conversion_status="not_convertible",
        )
        assert fm["tags"] == ["onedrive"]


class TestBuildSharePointFrontmatter:
    def test_basic_file(self):
        fm = build_sharepoint_frontmatter(
            file_name="plan.pptx",
            item_id="sp-item-1",
            size=120000,
            modified_time="2026-03-12T10:00:00Z",
            modified_by="Carol Davis",
            parent_path="Shared/Plans",
            web_url="https://sp.example.com/plan.pptx",
            site_name="Engineering Hub",
            drive_name="Documents",
            conversion_status="converted",
        )
        assert fm["type"] == "sharepoint_file"
        assert "sharepoint" in fm["tags"]
        assert "pptx" in fm["tags"]
        assert fm["site_name"] == "Engineering Hub"
        assert fm["drive_name"] == "Documents"
        assert fm["source"]["service"] == "sharepoint"
        assert fm["source"]["extractor"] == "m365-extract/sharepoint/1.0"


class TestBuildTeamsChannelFrontmatter:
    def test_basic_channel(self):
        fm = build_teams_channel_frontmatter(
            team_name="Engineering",
            channel_name="General",
            channel_id="ch-123",
            last_message_time="2026-03-12T10:00:00Z",
        )
        assert fm["type"] == "teams_channel"
        assert fm["title"] == "Engineering / General"
        assert fm["team"] == "Engineering"
        assert fm["channel"] == "General"


class TestBuildContactFrontmatter:
    def test_basic_contact(self):
        fm = build_contact_frontmatter(
            display_name="Jane Smith",
            contact_id="contact-123",
            email_addresses=["jane@contoso.com"],
            phones=["+1-555-0100"],
            company="Contoso Ltd",
            job_title="VP Engineering",
            department="Engineering",
            categories=["VIP"],
        )
        assert fm["title"] == "Jane Smith"
        assert fm["type"] == "contact"
        assert "contact" in fm["tags"]
        assert "vip" in fm["tags"]
        assert fm["email"] == ["jane@contoso.com"]
        assert fm["phone"] == ["+1-555-0100"]
        assert fm["company"] == "Contoso Ltd"
        assert fm["job_title"] == "VP Engineering"
        assert fm["department"] == "Engineering"
        assert fm["source"]["service"] == "people"
        assert fm["source"]["extractor"] == "m365-extract/contacts/1.0"
        assert fm["permalink"].startswith("contact-jane-smith-")

    def test_empty_optional_fields_omitted(self):
        fm = build_contact_frontmatter(
            display_name="Minimal Contact",
            contact_id="contact-min",
            email_addresses=[],
            phones=[],
            company="",
            job_title="",
            department="",
            categories=[],
        )
        assert "email" not in fm
        assert "phone" not in fm
        assert "company" not in fm
        assert "job_title" not in fm
        assert "department" not in fm

    def test_multiple_categories_as_tags(self):
        fm = build_contact_frontmatter(
            display_name="Tagged Contact",
            contact_id="contact-tags",
            email_addresses=[],
            phones=[],
            company="",
            job_title="",
            department="",
            categories=["VIP", "Engineering Team"],
        )
        assert "vip" in fm["tags"]
        assert "engineering-team" in fm["tags"]


class TestBuildDirectoryUserFrontmatter:
    def test_basic_user(self):
        fm = build_directory_user_frontmatter(
            display_name="Jane Smith",
            user_id="user-123",
            email="jane@contoso.com",
            upn="jane@contoso.com",
            job_title="VP Engineering",
            department="Engineering",
            office="Building 1",
            city="Seattle",
            manager_link="[[directory-john-doe-abc123]]",
            direct_reports_links=["[[directory-alice-wong-def456]]"],
        )
        assert fm["title"] == "Jane Smith"
        assert fm["type"] == "directory_user"
        assert "directory" in fm["tags"]
        assert "engineering" in fm["tags"]
        assert fm["email"] == "jane@contoso.com"
        assert fm["upn"] == "jane@contoso.com"
        assert fm["job_title"] == "VP Engineering"
        assert fm["manager"] == "[[directory-john-doe-abc123]]"
        assert fm["direct_reports"] == ["[[directory-alice-wong-def456]]"]
        assert fm["source"]["service"] == "directory"
        assert fm["source"]["extractor"] == "m365-extract/directory/1.0"
        assert fm["permalink"].startswith("directory-jane-smith-")

    def test_empty_optional_fields_omitted(self):
        fm = build_directory_user_frontmatter(
            display_name="Minimal User",
            user_id="user-min",
            email="min@contoso.com",
            upn="min@contoso.com",
            job_title="",
            department="",
            office="",
            city="",
            manager_link="",
            direct_reports_links=[],
        )
        assert "job_title" not in fm
        assert "department" not in fm
        assert "office" not in fm
        assert "city" not in fm
        assert "manager" not in fm
        assert "direct_reports" not in fm

    def test_department_as_tag(self):
        fm = build_directory_user_frontmatter(
            display_name="Dept User",
            user_id="user-dept",
            email="dept@contoso.com",
            upn="dept@contoso.com",
            job_title="",
            department="Product Design",
            office="",
            city="",
            manager_link="",
            direct_reports_links=[],
        )
        assert "product-design" in fm["tags"]
