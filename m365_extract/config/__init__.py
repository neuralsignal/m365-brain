"""Config package -- re-exports schema, loader, and errors for convenient imports."""

from m365_extract.config.errors import ConfigError
from m365_extract.config.loader import load_config
from m365_extract.config.schema import (
    AuthConfig,
    AzureBlobStorageConfig,
    CalendarExtractorConfig,
    Config,
    ContactsExtractorConfig,
    DirectoryExtractorConfig,
    EmailExtractorConfig,
    ExtractorsConfig,
    GraphConfig,
    LocalStorageConfig,
    OneDriveExtractorConfig,
    ServiceConfig,
    SharePointExtractorConfig,
    StateConfig,
    StorageConfig,
    TeamsChannelsExtractorConfig,
    TeamsChatsExtractorConfig,
    WebConfig,
)

__all__ = [
    "AuthConfig",
    "AzureBlobStorageConfig",
    "CalendarExtractorConfig",
    "Config",
    "ConfigError",
    "ContactsExtractorConfig",
    "DirectoryExtractorConfig",
    "EmailExtractorConfig",
    "ExtractorsConfig",
    "GraphConfig",
    "LocalStorageConfig",
    "OneDriveExtractorConfig",
    "ServiceConfig",
    "SharePointExtractorConfig",
    "StateConfig",
    "StorageConfig",
    "TeamsChannelsExtractorConfig",
    "TeamsChatsExtractorConfig",
    "WebConfig",
    "load_config",
]
