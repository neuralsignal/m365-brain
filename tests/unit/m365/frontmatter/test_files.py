"""Tests for the OneDrive and SharePoint file frontmatter builders."""

from __future__ import annotations

import re

from hypothesis import given
from hypothesis import strategies as st

from m365_brain.m365.frontmatter.files import (
    OneDriveFileData,
    SharePointFileData,
    build_onedrive_frontmatter,
    build_sharepoint_frontmatter,
)

SHARED_KEYS = {
    "title",
    "permalink",
    "type",
    "tags",
    "file_name",
    "file_size",
    "modified",
    "modified_by",
    "parent_path",
    "content_status",
    "source",
    "status",
}

FILE_NAMES = st.sampled_from(["report.docx", "Plan.PPTX", "notes", "archive.tar.gz", "budget.xlsx"])

ONEDRIVE_FILES = st.builds(
    OneDriveFileData,
    file_name=FILE_NAMES,
    item_id=st.text(min_size=1, max_size=30),
    size=st.integers(min_value=0, max_value=10**9),
    modified_time=st.just("2026-03-12T10:00:00Z"),
    modified_by=st.text(max_size=30),
    parent_path=st.text(max_size=40),
    web_url=st.text(max_size=40),
    content_status=st.sampled_from(["pending", "converted", "failed", "not_convertible"]),
)

SHAREPOINT_FILES = st.builds(
    SharePointFileData,
    file_name=FILE_NAMES,
    item_id=st.text(min_size=1, max_size=30),
    size=st.integers(min_value=0, max_value=10**9),
    modified_time=st.just("2026-03-12T10:00:00Z"),
    modified_by=st.text(max_size=30),
    parent_path=st.text(max_size=40),
    web_url=st.text(max_size=40),
    site_name=st.text(max_size=30),
    drive_name=st.text(max_size=30),
    content_status=st.sampled_from(["pending", "converted", "failed", "not_convertible"]),
)


class TestFileFrontmatterProperties:
    @given(ONEDRIVE_FILES)
    def test_onedrive_shape(self, data: OneDriveFileData):
        fm = build_onedrive_frontmatter(data)

        assert set(fm) == SHARED_KEYS
        assert fm["type"] == "onedrive_file"
        assert fm["status"] == "raw"
        assert fm["title"] == data.file_name == fm["file_name"]
        assert fm["file_size"] == data.size
        assert fm["content_status"] == data.content_status
        assert fm["source"]["service"] == "onedrive"
        assert fm["source"]["extractor"] == "m365-brain/onedrive/1.0"
        assert re.fullmatch(r"onedrive-[a-z0-9-]+-[0-9a-f]{6}", fm["permalink"])

    @given(SHAREPOINT_FILES)
    def test_sharepoint_shape(self, data: SharePointFileData):
        fm = build_sharepoint_frontmatter(data)

        assert set(fm) == SHARED_KEYS | {"site_name", "drive_name"}
        assert fm["type"] == "sharepoint_file"
        assert fm["site_name"] == data.site_name
        assert fm["drive_name"] == data.drive_name
        assert fm["source"]["service"] == "sharepoint"
        assert fm["source"]["extractor"] == "m365-brain/sharepoint/1.0"
        assert re.fullmatch(r"sharepoint-[a-z0-9-]+-[0-9a-f]{6}", fm["permalink"])

    @given(ONEDRIVE_FILES)
    def test_tags_are_lowercase_extension_after_prefix(self, data: OneDriveFileData):
        fm = build_onedrive_frontmatter(data)

        assert all(isinstance(tag, str) for tag in fm["tags"])
        assert fm["tags"][0] == "onedrive"
        if "." in data.file_name:
            assert fm["tags"] == ["onedrive", data.file_name.rsplit(".", 1)[-1].lower()]
        else:
            assert fm["tags"] == ["onedrive"]


class TestFileFrontmatterShapes:
    def test_uppercase_extension_lowercased_but_title_preserved(self):
        fm = build_sharepoint_frontmatter(
            SharePointFileData(
                file_name="Q1 Plan.PPTX",
                item_id="sp-1",
                size=120000,
                modified_time="2026-03-12T10:00:00Z",
                modified_by="Carol Davis",
                parent_path="Shared/Plans",
                web_url="https://sp.example.com/plan.pptx",
                site_name="Engineering Hub",
                drive_name="Documents",
                content_status="converted",
            )
        )

        assert fm["title"] == "Q1 Plan.PPTX"
        assert fm["tags"] == ["sharepoint", "pptx"]
        assert fm["permalink"].startswith("sharepoint-q1-plan-pptx-")
        assert fm["parent_path"] == "Shared/Plans"
        assert fm["source"]["url"] == "https://sp.example.com/plan.pptx"

    def test_file_without_extension_has_single_tag(self):
        fm = build_onedrive_frontmatter(
            OneDriveFileData(
                file_name="Makefile",
                item_id="od-1",
                size=0,
                modified_time="2026-03-12T10:00:00Z",
                modified_by="",
                parent_path="",
                web_url="",
                content_status="not_convertible",
            )
        )

        assert fm["tags"] == ["onedrive"]
        assert fm["file_size"] == 0
        assert fm["content_status"] == "not_convertible"

    def test_dotfile_is_treated_as_pure_extension(self):
        """`.gitignore` has no stem, so the whole name becomes the extension tag."""
        fm = build_onedrive_frontmatter(
            OneDriveFileData(
                file_name=".gitignore",
                item_id="od-2",
                size=42,
                modified_time="2026-03-12T10:00:00Z",
                modified_by="Bob",
                parent_path="repo",
                web_url="",
                content_status="pending",
            )
        )

        assert fm["tags"] == ["onedrive", "gitignore"]

    def test_trailing_dot_yields_empty_extension_tag(self):
        """A name ending in `.` is truthy-checked as `"."`, so an empty tag is emitted."""
        fm = build_onedrive_frontmatter(
            OneDriveFileData(
                file_name="report.",
                item_id="od-3",
                size=10,
                modified_time="2026-03-12T10:00:00Z",
                modified_by="Bob",
                parent_path="",
                web_url="",
                content_status="pending",
            )
        )

        assert fm["tags"] == ["onedrive", ""]

    def test_same_item_id_yields_different_permalinks_per_service(self):
        onedrive = build_onedrive_frontmatter(
            OneDriveFileData(
                file_name="shared.docx",
                item_id="item-1",
                size=1,
                modified_time="2026-03-12T10:00:00Z",
                modified_by="Bob",
                parent_path="",
                web_url="",
                content_status="pending",
            )
        )
        sharepoint = build_sharepoint_frontmatter(
            SharePointFileData(
                file_name="shared.docx",
                item_id="item-1",
                size=1,
                modified_time="2026-03-12T10:00:00Z",
                modified_by="Bob",
                parent_path="",
                web_url="",
                site_name="Hub",
                drive_name="Documents",
                content_status="pending",
            )
        )

        assert onedrive["permalink"] != sharepoint["permalink"]
        # only the service prefix differs — the slug and hash are identical
        assert onedrive["permalink"].removeprefix("onedrive-") == sharepoint["permalink"].removeprefix("sharepoint-")
