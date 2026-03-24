"""Config schema -- frozen dataclass definitions for every config section."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AuthConfig:
    client_id: str
    tenant_id: str
    scopes: list[str]
    token_cache_path: str
    client_secret: str | None


@dataclass(frozen=True)
class ServiceConfig:
    mode: str
    log_level: str
    json_logs: bool


@dataclass(frozen=True)
class LocalStorageConfig:
    base_path: str


@dataclass(frozen=True)
class AzureBlobStorageConfig:
    connection_string: str
    container_name: str
    prefix: str


@dataclass(frozen=True)
class StorageConfig:
    backend: str
    local: LocalStorageConfig | None
    azure_blob: AzureBlobStorageConfig | None


@dataclass(frozen=True)
class GraphConfig:
    max_retries: int
    backoff_base_ms: int
    timeout_seconds: int
    max_pages: int


@dataclass(frozen=True)
class StateConfig:
    state_file_path: str


@dataclass(frozen=True)
class EmailExtractorConfig:
    enabled: bool
    poll_interval_minutes: int
    folders: list[str]
    lookback_days: int
    max_items_per_sync: int


@dataclass(frozen=True)
class CalendarExtractorConfig:
    enabled: bool
    poll_interval_minutes: int
    lookback_days: int
    forward_days: int


@dataclass(frozen=True)
class TeamsChatsExtractorConfig:
    enabled: bool
    poll_interval_minutes: int
    max_messages_per_chat: int


@dataclass(frozen=True)
class TeamsChannelsExtractorConfig:
    enabled: bool
    poll_interval_minutes: int


@dataclass(frozen=True)
class OneDriveExtractorConfig:
    enabled: bool
    poll_interval_minutes: int
    eager_convert_patterns: list[str]
    convertible_extensions: list[str]
    max_file_size_mb: int


@dataclass(frozen=True)
class SharePointExtractorConfig:
    enabled: bool
    poll_interval_minutes: int
    eager_convert_patterns: list[str]
    convertible_extensions: list[str]
    max_file_size_mb: int


@dataclass(frozen=True)
class ContactsExtractorConfig:
    enabled: bool
    poll_interval_minutes: int
    max_items_per_sync: int
    include_contact_folders: bool


@dataclass(frozen=True)
class DirectoryExtractorConfig:
    enabled: bool
    poll_interval_minutes: int
    include_manager_chain: bool
    include_direct_reports: bool
    only_active_users: bool


@dataclass(frozen=True)
class ExtractorsConfig:
    email: EmailExtractorConfig
    calendar: CalendarExtractorConfig
    teams_chats: TeamsChatsExtractorConfig
    teams_channels: TeamsChannelsExtractorConfig
    onedrive: OneDriveExtractorConfig
    sharepoint: SharePointExtractorConfig
    contacts: ContactsExtractorConfig
    directory: DirectoryExtractorConfig


@dataclass(frozen=True)
class WebConfig:
    host: str
    port: int
    secret_key: str
    fernet_key: str
    db_path: str
    session_timeout_minutes: int


@dataclass(frozen=True)
class Config:
    auth: AuthConfig
    service: ServiceConfig
    storage: StorageConfig
    graph: GraphConfig
    state: StateConfig
    extractors: ExtractorsConfig
    converters: dict
    web: WebConfig | None
