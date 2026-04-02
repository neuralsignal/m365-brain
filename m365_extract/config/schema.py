"""Config schema -- frozen pydantic models for every config section."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class AuthConfig(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)
    client_id: str
    tenant_id: str
    scopes: list[str]
    token_cache_path: str
    client_secret: str | None = None


class ServiceConfig(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)
    mode: str
    log_level: str
    json_logs: bool
    continuous_poll_seconds: int
    max_consecutive_auth_failures: int


class LocalStorageConfig(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)
    base_path: str


class AzureBlobStorageConfig(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)
    connection_string: str
    container_name: str
    prefix: str


class StorageConfig(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)
    backend: str
    local: LocalStorageConfig | None = None
    azure_blob: AzureBlobStorageConfig | None = None


class GraphConfig(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)
    max_retries: int
    backoff_base_ms: int
    timeout_seconds: int
    max_pages: int
    max_retry_after_seconds: float
    error_message_max_length: int


class StateConfig(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)
    state_file_path: str


class EmailExtractorConfig(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)
    enabled: bool
    poll_interval_minutes: int
    folders: list[str]
    lookback_days: int
    max_items_per_sync: int


class CalendarExtractorConfig(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)
    enabled: bool
    poll_interval_minutes: int
    lookback_days: int
    forward_days: int


class TeamsChatsExtractorConfig(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)
    enabled: bool
    poll_interval_minutes: int
    max_messages_per_chat: int


class TeamsChannelsExtractorConfig(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)
    enabled: bool
    poll_interval_minutes: int


class OneDriveExtractorConfig(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)
    enabled: bool
    poll_interval_minutes: int
    eager_convert_patterns: list[str]
    convertible_extensions: list[str]
    max_file_size_mb: int


class SharePointExtractorConfig(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)
    enabled: bool
    poll_interval_minutes: int
    eager_convert_patterns: list[str]
    convertible_extensions: list[str]
    max_file_size_mb: int


class ContactsExtractorConfig(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)
    enabled: bool
    poll_interval_minutes: int
    max_items_per_sync: int
    include_contact_folders: bool


class DirectoryExtractorConfig(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)
    enabled: bool
    poll_interval_minutes: int
    include_manager_chain: bool
    include_direct_reports: bool
    only_active_users: bool


class ExtractorsConfig(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)
    email: EmailExtractorConfig
    calendar: CalendarExtractorConfig
    teams_chats: TeamsChatsExtractorConfig
    teams_channels: TeamsChannelsExtractorConfig
    onedrive: OneDriveExtractorConfig
    sharepoint: SharePointExtractorConfig
    contacts: ContactsExtractorConfig
    directory: DirectoryExtractorConfig


class MediaConfig(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)
    extract_images: bool
    image_format: str
    image_max_dimension: int


class ExtractionConfig(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)
    timeout_seconds: int
    max_file_size_mb: int
    xlsx_max_rows_per_sheet: int


class ConvertersConfig(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)
    backends: dict[str, str]
    extraction: ExtractionConfig
    media: MediaConfig | None = None
    slug_max_length: int
    hash_length: int


class WebConfig(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)
    host: str
    port: int
    secret_key: str
    fernet_key: str
    db_path: str
    session_timeout_minutes: int
    db_url: str
    admin_emails: list[str]


class WorkerConfig(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)
    max_concurrent_jobs: int
    poll_interval_seconds: int


class Config(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True)
    auth: AuthConfig
    service: ServiceConfig
    storage: StorageConfig
    graph: GraphConfig
    state: StateConfig
    extractors: ExtractorsConfig
    converters: ConvertersConfig
    web: WebConfig | None = None
    worker: WorkerConfig | None = None
