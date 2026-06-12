"""Storage backends for m365-extract."""

from __future__ import annotations

from pathlib import Path

from m365_extract.config import StorageConfig
from m365_extract.config.errors import ConfigError
from m365_extract.storage.base import StorageBackend
from m365_extract.validation import validate_user_id


def create_storage(config: StorageConfig) -> StorageBackend:
    """Create a storage backend from config. Crashes on unknown or misconfigured backend."""
    if config.backend == "local":
        if config.local is None:
            raise ConfigError("backend is 'local' but storage.local section is missing")
        from m365_extract.storage.local import LocalBackend

        return LocalBackend(config.local.base_path)

    if config.backend == "azure_blob":
        if config.azure_blob is None:
            raise ConfigError("backend is 'azure_blob' but storage.azure_blob section is missing")
        from m365_extract.storage.azure_blob import AzureBlobBackend

        return AzureBlobBackend(
            connection_string=config.azure_blob.connection_string,
            container_name=config.azure_blob.container_name,
            prefix=config.azure_blob.prefix,
        )

    raise ConfigError(f"unknown storage backend '{config.backend}'")


def create_user_storage(config: StorageConfig, user_id: str) -> StorageBackend:
    """Create a storage backend with per-user isolation.

    Appends ``user_id`` to the storage prefix (azure_blob) or base_path (local)
    so each user's synced data lives in its own subdirectory.
    """
    validate_user_id(user_id)
    if config.backend == "local":
        if config.local is None:
            raise ConfigError("backend is 'local' but storage.local section is missing")
        from m365_extract.storage.local import LocalBackend

        user_path = str(Path(config.local.base_path) / user_id)
        return LocalBackend(user_path)

    if config.backend == "azure_blob":
        if config.azure_blob is None:
            raise ConfigError("backend is 'azure_blob' but storage.azure_blob section is missing")
        from m365_extract.storage.azure_blob import AzureBlobBackend

        base_prefix = config.azure_blob.prefix.rstrip("/")
        user_prefix = f"{base_prefix}/{user_id}"
        return AzureBlobBackend(
            connection_string=config.azure_blob.connection_string,
            container_name=config.azure_blob.container_name,
            prefix=user_prefix,
        )

    raise ConfigError(f"unknown storage backend '{config.backend}'")
