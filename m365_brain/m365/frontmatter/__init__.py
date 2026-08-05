"""Per-extractor frontmatter builder functions for Obsidian-compatible markdown."""

from m365_brain.m365.frontmatter.calendar import (
    CalendarEventData,
    attendee_relations,
    build_calendar_frontmatter,
)
from m365_brain.m365.frontmatter.email import EmailData, build_email_frontmatter
from m365_brain.m365.frontmatter.files import (
    OneDriveFileData,
    SharePointFileData,
    build_onedrive_frontmatter,
    build_sharepoint_frontmatter,
)
from m365_brain.m365.frontmatter.people import (
    ContactData,
    DirectoryUserData,
    address_observations,
    build_contact_frontmatter,
    build_directory_user_frontmatter,
)
from m365_brain.m365.frontmatter.teams import (
    TeamsChannelData,
    TeamsChatData,
    build_teams_channel_frontmatter,
    build_teams_chat_frontmatter,
    participant_relations,
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
    "address_observations",
    "attendee_relations",
    "build_calendar_frontmatter",
    "build_contact_frontmatter",
    "build_directory_user_frontmatter",
    "build_email_frontmatter",
    "build_onedrive_frontmatter",
    "build_sharepoint_frontmatter",
    "build_teams_channel_frontmatter",
    "build_teams_chat_frontmatter",
    "participant_relations",
]
