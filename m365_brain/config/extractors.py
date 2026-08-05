"""Per-extractor configuration: enable flag, poll interval, and options.

`EXTRACTOR_NAMES` is the one place the eight implemented extractors are
enumerated. `vault.extractor_dirs` validates against it, so a config that
names a directory for an extractor this package does not implement -- or omits
one it does -- crashes at load with the name in the message.
"""

from __future__ import annotations

from pydantic import BaseModel, field_validator

from m365_brain.config.base import SECTION_MODEL_CONFIG

EXTRACTOR_NAMES: tuple[str, ...] = (
    "email",
    "calendar",
    "contacts",
    "directory",
    "onedrive",
    "sharepoint",
    "teams_chats",
    "teams_channels",
)


class MailboxConfig(BaseModel):
    model_config = SECTION_MODEL_CONFIG
    address: str
    folders: list[str] | None
    output_subdir: str


class EmailExtractorConfig(BaseModel):
    model_config = SECTION_MODEL_CONFIG
    enabled: bool
    poll_interval_minutes: int
    mailboxes: list[MailboxConfig]
    lookback_days: int
    max_items_per_sync: int
    download_attachments: bool
    max_attachment_size_mb: int
    attachment_convert_extensions: list[str]


class CalendarExtractorConfig(BaseModel):
    model_config = SECTION_MODEL_CONFIG
    enabled: bool
    poll_interval_minutes: int
    lookback_days: int
    forward_days: int


class TeamsChatsExtractorConfig(BaseModel):
    model_config = SECTION_MODEL_CONFIG
    enabled: bool
    poll_interval_minutes: int
    max_messages_per_chat: int
    download_attachments: bool
    download_inline_images: bool
    max_attachment_size_mb: int
    attachment_convert_extensions: list[str]


class ExplicitChannel(BaseModel):
    """A channel to sync without Graph discovery.

    Names must come from config: fetching displayNames requires the
    Team.ReadBasic.All / Channel.ReadBasic.All scopes that explicit mode
    exists to avoid.
    """

    model_config = SECTION_MODEL_CONFIG
    team_id: str
    channel_id: str
    team_name: str
    channel_name: str


class TeamsChannelsExtractorConfig(BaseModel):
    model_config = SECTION_MODEL_CONFIG
    enabled: bool
    poll_interval_minutes: int
    max_messages_per_channel: int
    download_attachments: bool
    download_inline_images: bool
    max_attachment_size_mb: int
    attachment_convert_extensions: list[str]
    channels: list[ExplicitChannel] | None

    @field_validator("channels")
    @classmethod
    def _channels_not_empty(cls, value: list[ExplicitChannel] | None) -> list[ExplicitChannel] | None:
        if value is not None and len(value) == 0:
            raise ValueError("explicit mode with zero channels is a misconfiguration — use null for discovery mode")
        return value


class OneDriveExtractorConfig(BaseModel):
    model_config = SECTION_MODEL_CONFIG
    enabled: bool
    poll_interval_minutes: int
    eager_convert_patterns: list[str]
    convertible_extensions: list[str]
    max_file_size_mb: int


class SharePointExtractorConfig(BaseModel):
    model_config = SECTION_MODEL_CONFIG
    enabled: bool
    poll_interval_minutes: int
    eager_convert_patterns: list[str]
    convertible_extensions: list[str]
    max_file_size_mb: int


class ContactsExtractorConfig(BaseModel):
    model_config = SECTION_MODEL_CONFIG
    enabled: bool
    poll_interval_minutes: int
    max_items_per_sync: int
    include_contact_folders: bool


class DirectoryExtractorConfig(BaseModel):
    model_config = SECTION_MODEL_CONFIG
    enabled: bool
    poll_interval_minutes: int
    include_manager_chain: bool
    include_direct_reports: bool
    only_active_users: bool


class ExtractorsConfig(BaseModel):
    model_config = SECTION_MODEL_CONFIG
    email: EmailExtractorConfig
    calendar: CalendarExtractorConfig
    teams_chats: TeamsChatsExtractorConfig
    teams_channels: TeamsChannelsExtractorConfig
    onedrive: OneDriveExtractorConfig
    sharepoint: SharePointExtractorConfig
    contacts: ContactsExtractorConfig
    directory: DirectoryExtractorConfig

    auth_profile: str | None = None
    """Which `auth.profiles` entry every extractor authenticates with.

    `None` is meaningful and is the single-app path: authenticate with the
    `auth:` section itself, which is what one Entra app for all eight
    extractors looks like. Name a profile only once several apps exist. The
    root validator checks that the name resolves.
    """
