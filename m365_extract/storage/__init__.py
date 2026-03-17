"""Storage backends for m365-extract."""

from __future__ import annotations

import sys

from m365_extract.config import StorageConfig
from m365_extract.storage.base import StorageBackend


def create_storage(config: StorageConfig) -> StorageBackend:
    """Create a storage backend from config. Crashes on unknown or misconfigured backend."""
    if config.backend == "local":
        if config.local is None:
            print("Config error: backend is 'local' but storage.local section is missing", file=sys.stderr)
            raise SystemExit(1)
        from m365_extract.storage.local import LocalBackend

        return LocalBackend(config.local.base_path)

    if config.backend == "azure_blob":
        if config.azure_blob is None:
            print("Config error: backend is 'azure_blob' but storage.azure_blob section is missing", file=sys.stderr)
            raise SystemExit(1)
        from m365_extract.storage.azure_blob import AzureBlobBackend

        return AzureBlobBackend(
            connection_string=config.azure_blob.connection_string,
            container_name=config.azure_blob.container_name,
            prefix=config.azure_blob.prefix,
        )

    print(f"Config error: unknown storage backend '{config.backend}'", file=sys.stderr)
    raise SystemExit(1)
