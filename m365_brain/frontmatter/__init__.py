"""Per-extractor frontmatter builder functions for Obsidian-compatible markdown."""

from m365_brain.frontmatter.calendar import CalendarEventData, build_calendar_frontmatter
from m365_brain.frontmatter.email import EmailData, build_email_frontmatter
from m365_brain.frontmatter.files import (
    OneDriveFileData,
    SharePointFileData,
    build_onedrive_frontmatter,
    build_sharepoint_frontmatter,
)
from m365_brain.frontmatter.people import (
    ContactData,
    DirectoryUserData,
    build_contact_frontmatter,
    build_directory_user_frontmatter,
)
from m365_brain.frontmatter.teams import (
    TeamsChannelData,
    TeamsChatData,
    build_teams_channel_frontmatter,
    build_teams_chat_frontmatter,
)

__all__ = [
    "CalendarEventData",
    "ContactData",
    "DirectoryUserData",
    "EmailData",
    "OneDriveFileData",
    "SharePointFileData",
    "TeamsChannelData",
    "TeamsChatData",
    "build_calendar_frontmatter",
    "build_contact_frontmatter",
    "build_directory_user_frontmatter",
    "build_email_frontmatter",
    "build_onedrive_frontmatter",
    "build_sharepoint_frontmatter",
    "build_teams_channel_frontmatter",
    "build_teams_chat_frontmatter",
]
