"""Config package -- re-exports schema and loader for backwards-compatible imports."""

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
)

__all__ = [
    "AuthConfig",
    "AzureBlobStorageConfig",
    "CalendarExtractorConfig",
    "Config",
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
    "load_config",
]
