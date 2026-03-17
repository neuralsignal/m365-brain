"""Azure Blob Storage backend. Stores markdown files as blobs under a container + prefix."""

from __future__ import annotations

import structlog

log = structlog.get_logger()


class AzureBlobBackend:
    """Azure Blob Storage backend implementing the StorageBackend protocol.

    All file paths are prefixed with ``self._prefix`` to enable per-user or
    per-tenant isolation within a shared container.
    """

    def __init__(self, connection_string: str, container_name: str, prefix: str) -> None:
        from azure.storage.blob import ContainerClient

        self._container_client = ContainerClient.from_connection_string(
            connection_string,
            container_name,
        )
        self._prefix = prefix.rstrip("/") + "/" if prefix else ""
        # Ensure container exists
        import contextlib

        from azure.core.exceptions import ResourceExistsError

        with contextlib.suppress(ResourceExistsError):
            self._container_client.create_container()

    def _blob_name(self, path: str) -> str:
        """Build the full blob name by prepending the configured prefix."""
        return self._prefix + path

    def write_file(self, path: str, content: str) -> None:
        """Write UTF-8 content to a blob, overwriting if it exists."""
        blob_name = self._blob_name(path)
        self._container_client.upload_blob(
            blob_name,
            content.encode("utf-8"),
            overwrite=True,
        )

    def read_file(self, path: str) -> str:
        """Read and return UTF-8 content from a blob."""
        blob_name = self._blob_name(path)
        blob_client = self._container_client.get_blob_client(blob_name)
        data = blob_client.download_blob().readall()
        return data.decode("utf-8")

    def file_exists(self, path: str) -> bool:
        """Return True if a blob exists at the given path."""
        from azure.core.exceptions import ResourceNotFoundError

        blob_name = self._blob_name(path)
        blob_client = self._container_client.get_blob_client(blob_name)
        try:
            blob_client.get_blob_properties()
            return True
        except ResourceNotFoundError:
            return False

    def list_files(self, prefix: str) -> list[str]:
        """List all blob paths under the given prefix, stripping the internal prefix."""
        full_prefix = self._prefix + prefix
        blobs = self._container_client.list_blobs(name_starts_with=full_prefix)
        prefix_len = len(self._prefix)
        return [blob.name[prefix_len:] for blob in blobs]

    def delete_file(self, path: str) -> None:
        """Delete a blob. No error if it does not exist."""
        import contextlib

        from azure.core.exceptions import ResourceNotFoundError

        blob_name = self._blob_name(path)
        blob_client = self._container_client.get_blob_client(blob_name)
        with contextlib.suppress(ResourceNotFoundError):
            blob_client.delete_blob()
