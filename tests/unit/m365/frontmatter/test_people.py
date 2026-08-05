"""Tests for the contact and directory-user frontmatter builders."""

from __future__ import annotations

import re

from hypothesis import given
from hypothesis import strategies as st

from m365_brain.m365.frontmatter.people import (
    ContactData,
    DirectoryUserData,
    build_contact_frontmatter,
    build_directory_user_frontmatter,
)

CONTACT_BASE_KEYS = {"title", "permalink", "type", "tags", "source", "status"}
DIRECTORY_BASE_KEYS = CONTACT_BASE_KEYS | {"email", "upn"}

CONTACTS = st.builds(
    ContactData,
    display_name=st.text(min_size=1, max_size=40),
    contact_id=st.text(min_size=1, max_size=30),
    email_addresses=st.lists(st.emails(), max_size=3),
    phones=st.lists(st.text(min_size=1, max_size=15), max_size=3),
    company=st.text(max_size=30),
    job_title=st.text(max_size=30),
    department=st.text(max_size=30),
    categories=st.lists(st.sampled_from(["VIP", "Engineering Team", "Board"]), max_size=3),
)

DIRECTORY_USERS = st.builds(
    DirectoryUserData,
    display_name=st.text(min_size=1, max_size=40),
    user_id=st.text(min_size=1, max_size=30),
    email=st.emails(),
    upn=st.emails(),
    job_title=st.text(max_size=30),
    department=st.sampled_from(["", "Engineering", "Product Design"]),
    office=st.text(max_size=20),
    city=st.text(max_size=20),
    manager_link=st.text(max_size=30),
    direct_reports_links=st.lists(st.text(min_size=1, max_size=30), max_size=3),
)


class TestPeopleFrontmatterProperties:
    @given(CONTACTS)
    def test_contact_optional_keys_track_truthiness(self, data: ContactData):
        """Every optional contact field appears exactly when its source value is truthy."""
        fm = build_contact_frontmatter(data)

        assert set(fm) >= CONTACT_BASE_KEYS
        assert fm["type"] == "contact"
        assert fm["status"] == "raw"
        assert fm["source"]["service"] == "people"
        assert fm["source"]["extractor"] == "m365-brain/contacts/1.0"
        assert re.fullmatch(r"contact-[a-z0-9-]+-[0-9a-f]{6}", fm["permalink"])
        assert ("email" in fm) is bool(data.email_addresses)
        assert ("phone" in fm) is bool(data.phones)
        assert ("company" in fm) is bool(data.company)
        assert ("job_title" in fm) is bool(data.job_title)
        assert ("department" in fm) is bool(data.department)

    @given(CONTACTS)
    def test_contact_tags_are_lowercase_categories(self, data: ContactData):
        fm = build_contact_frontmatter(data)

        assert all(isinstance(tag, str) for tag in fm["tags"])
        assert fm["tags"][0] == "contact"
        assert len(fm["tags"]) == 1 + len(data.categories)
        assert all(tag == tag.lower() and " " not in tag for tag in fm["tags"])

    @given(DIRECTORY_USERS)
    def test_directory_user_always_emits_identity_keys(self, data: DirectoryUserData):
        """`email`/`upn` are unconditional here, unlike the contact builder."""
        fm = build_directory_user_frontmatter(data)

        assert set(fm) >= DIRECTORY_BASE_KEYS
        assert fm["type"] == "directory_user"
        assert fm["email"] == data.email
        assert fm["upn"] == data.upn
        assert fm["source"]["service"] == "directory"
        assert fm["source"]["extractor"] == "m365-brain/directory/1.0"
        assert re.fullmatch(r"directory-[a-z0-9-]+-[0-9a-f]{6}", fm["permalink"])
        assert fm["tags"][0] == "directory"
        assert (len(fm["tags"]) == 2) is bool(data.department)


class TestPeopleFrontmatterShapes:
    def test_category_tag_keeps_non_alphanumerics(self):
        """Category tags are lower/space-replaced only — they are not run through `slugify`."""
        fm = build_contact_frontmatter(
            ContactData(
                display_name="Jane Smith",
                contact_id="c-1",
                email_addresses=["jane@contoso.com"],
                phones=[],
                company="Contoso",
                job_title="",
                department="",
                categories=["R&D Team", "VIP"],
            )
        )

        assert fm["tags"] == ["contact", "r&d-team", "vip"]
        assert fm["email"] == ["jane@contoso.com"]
        assert "phone" not in fm
        assert fm["company"] == "Contoso"
        assert fm["permalink"].startswith("contact-jane-smith-")

    def test_contact_without_any_optional_field(self):
        fm = build_contact_frontmatter(
            ContactData(
                display_name="Minimal",
                contact_id="c-2",
                email_addresses=[],
                phones=[],
                company="",
                job_title="",
                department="",
                categories=[],
            )
        )

        assert set(fm) == CONTACT_BASE_KEYS
        assert fm["tags"] == ["contact"]
        assert "url" not in fm["source"]

    def test_directory_user_with_empty_email_still_emits_the_key(self):
        fm = build_directory_user_frontmatter(
            DirectoryUserData(
                display_name="Ghost User",
                user_id="u-1",
                email="",
                upn="",
                job_title="",
                department="",
                office="",
                city="",
                manager_link="",
                direct_reports_links=[],
            )
        )

        assert fm["email"] == ""
        assert fm["upn"] == ""
        assert set(fm) == DIRECTORY_BASE_KEYS
        assert fm["tags"] == ["directory"]

    def test_directory_user_department_appears_as_tag_and_field(self):
        fm = build_directory_user_frontmatter(
            DirectoryUserData(
                display_name="Dana Lee",
                user_id="u-2",
                email="dana@contoso.com",
                upn="dana@contoso.com",
                job_title="Staff Engineer",
                department="Product Design",
                office="Building 1",
                city="Zürich",
                manager_link="[[directory-john-doe-abc123]]",
                direct_reports_links=["[[directory-alice-wong-def456]]"],
            )
        )

        assert fm["tags"] == ["directory", "product-design"]
        assert fm["department"] == "Product Design"
        assert fm["office"] == "Building 1"
        assert fm["city"] == "Zürich"
        assert fm["manager"] == "[[directory-john-doe-abc123]]"
        assert fm["direct_reports"] == ["[[directory-alice-wong-def456]]"]
