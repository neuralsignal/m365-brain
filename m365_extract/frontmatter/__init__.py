"""Per-extractor frontmatter builder functions for Obsidian-compatible markdown."""

from m365_extract.frontmatter.calendar import build_calendar_frontmatter
from m365_extract.frontmatter.email import build_email_frontmatter
from m365_extract.frontmatter.files import build_onedrive_frontmatter, build_sharepoint_frontmatter
from m365_extract.frontmatter.people import build_contact_frontmatter, build_directory_user_frontmatter
from m365_extract.frontmatter.teams import build_teams_channel_frontmatter, build_teams_chat_frontmatter

__all__ = [
    "build_calendar_frontmatter",
    "build_contact_frontmatter",
    "build_directory_user_frontmatter",
    "build_email_frontmatter",
    "build_onedrive_frontmatter",
    "build_sharepoint_frontmatter",
    "build_teams_channel_frontmatter",
    "build_teams_chat_frontmatter",
]
