"""Storage backends for m365-brain, and the addressing that surrounds them.

A storage key is **relative by contract** -- see `vault/paths.py`. That holds
inside the library, where `StorageBackend` addresses a blob container and a
filesystem path would be meaningless. It stops holding the moment a key leaves
the process: on stdout, in a caller's hands, a relative key needs a base the
caller has no way to obtain. `resolve_key` and `storage_key` are that boundary,
in one place, so the join rule cannot be spelled two ways.
"""

from __future__ import annotations

from pathlib import Path

from m365_brain.config import AzureBlobStorageConfig, StorageConfig
from m365_brain.config.errors import ConfigError
from m365_brain.storage.base import StorageBackend
from m365_brain.validation import validate_user_id


def create_storage(config: StorageConfig) -> StorageBackend:
    """Create a storage backend from config. Crashes on unknown or misconfigured backend."""
    if config.backend == "local":
        if config.local is None:
            raise ConfigError("backend is 'local' but storage.local section is missing")
        from m365_brain.storage.local import LocalBackend

        return LocalBackend(config.local.base_path)

    if config.backend == "azure_blob":
        if config.azure_blob is None:
            raise ConfigError("backend is 'azure_blob' but storage.azure_blob section is missing")
        from m365_brain.storage.azure_blob import AzureBlobBackend

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
        from m365_brain.storage.local import LocalBackend

        user_path = str(Path(config.local.base_path) / user_id)
        return LocalBackend(user_path)

    if config.backend == "azure_blob":
        if config.azure_blob is None:
            raise ConfigError("backend is 'azure_blob' but storage.azure_blob section is missing")
        from m365_brain.storage.azure_blob import AzureBlobBackend

        base_prefix = config.azure_blob.prefix.rstrip("/")
        user_prefix = f"{base_prefix}/{user_id}"
        return AzureBlobBackend(
            connection_string=config.azure_blob.connection_string,
            container_name=config.azure_blob.container_name,
            prefix=user_prefix,
        )

    raise ConfigError(f"unknown storage backend '{config.backend}'")


def local_base_path(config: StorageConfig) -> Path:
    """`storage.local.base_path`, as the filesystem path stored keys hang off.

    Raises rather than guessing for a blob-backed vault: `StorageBackend` has
    `write_bytes` and no `read_bytes`, so there is no backend-agnostic way to
    hand a stored object back to something that wants a file. Say so instead of
    failing later inside a storage client's constructor.
    """
    if config.backend != "local" or config.local is None:
        raise ConfigError(
            f"this reads a stored file back off disk, and storage.backend is {config.backend!r}. "
            f"StorageBackend has write_bytes but no read_bytes, so there is nothing to read from "
            f"-- run it against a local vault."
        )
    return Path(config.local.base_path)


def _container_url(config: AzureBlobStorageConfig) -> str:
    """The container's URL, parsed by the SDK that owns the connection-string grammar.

    Constructing the client performs no request; `.url` is assembled from the
    parsed endpoint. Hand-parsing the connection string here would be a second
    implementation of a grammar with SAS, emulator and custom-domain forms in it.
    """
    from azure.storage.blob import ContainerClient

    client = ContainerClient.from_connection_string(config.connection_string.get_secret_value(), config.container_name)
    try:
        return client.url.rstrip("/")
    finally:
        client.close()


def _address_base(config: StorageConfig) -> str:
    """Everything `resolve_key` puts in front of a key, including the separator."""
    if config.backend == "local":
        return f"{str(local_base_path(config)).rstrip('/')}/"
    if config.backend == "azure_blob":
        if config.azure_blob is None:
            raise ConfigError("backend is 'azure_blob' but storage.azure_blob section is missing")
        prefix = config.azure_blob.prefix.strip("/")
        return f"{_container_url(config.azure_blob)}/" + (f"{prefix}/" if prefix else "")
    raise ConfigError(f"unknown storage backend '{config.backend}'")


def _carries_its_own_base(value: str) -> bool:
    """True when a value already says where it lives -- an absolute path or a URI.

    Resolving one of those again would prepend a second base, so this is what
    makes `resolve_key` idempotent: resolving twice is resolving once.
    """
    return "://" in value or Path(value).is_absolute()


def resolve_key(config: StorageConfig, key: str) -> str:
    """One storage-relative key as an address that needs no base from the caller.

    An absolute filesystem path for `local`, a blob URL for `azure_blob`. This
    is the only conversion the library performs in that direction, and it
    happens at the process boundary -- keys stay relative everywhere inside.
    """
    if _carries_its_own_base(key):
        return key
    return _address_base(config) + key


def storage_key(config: StorageConfig, address: str) -> str:
    """The inverse of `resolve_key`: an address back to the key it names.

    An address that does not sit under the configured base comes back
    unchanged. It is then simply a key that matches nothing, which is the
    honest answer for a path belonging to some other vault -- better than a
    substring guess that would resolve it to the wrong row.
    """
    if not _carries_its_own_base(address):
        return address
    base = _address_base(config)
    return address[len(base) :] if address.startswith(base) else address
