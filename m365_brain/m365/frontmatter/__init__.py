"""Per-extractor frontmatter builder functions for Obsidian-compatible markdown.

**No builder writes `status`.** Every one of the eight used to emit
`status: "raw"`, and the word is not this library's to spend: a consuming vault
carries an authored lifecycle vocabulary under exactly that key -- `completed`,
`in_progress`, `active`, `pending` -- and `observation.category` is one flat
namespace with no per-root or per-type partition. So the two shared a name, and
the library's share of it was 24,217 of 24,285 rows in the measured index,
every one of them the same constant, read by nothing anywhere in this package.
An agent asking "what is `pending`?" got a task in one breath and a mail in the
next; an agent asking "show me everything whose status changed" got 24,217 rows
that cannot change.

The value said "this file is upstream truth, rewritten by an extractor", which
is already stated -- and stated once -- by `source.extractor` and by the file's
position under `vault.layout.inbox`. A second spelling of a fact the corpus
already holds is duplication, not provenance. See
`tests/unit/m365/frontmatter/test_source_contract.py`, which fails if a builder
reaches for the key again.
"""

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
    MANAGER,
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
    "MANAGER",
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
