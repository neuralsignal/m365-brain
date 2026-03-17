"""Unit tests for Azure Blob Storage backend (mocked, no Azurite needed)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from azure.core.exceptions import ResourceNotFoundError

# Patch target: the import location inside azure_blob.py's __init__
_CONTAINER_CLIENT = "azure.storage.blob.ContainerClient"


def _make_backend(prefix: str) -> tuple:
    """Create an AzureBlobBackend with mocked Azure SDK. Returns (backend, mock_client)."""
    with patch(_CONTAINER_CLIENT) as mock_cls:
        mock_client = MagicMock()
        mock_cls.from_connection_string.return_value = mock_client
        from m365_extract.storage.azure_blob import AzureBlobBackend

        backend = AzureBlobBackend("conn-string", "container", prefix)
    return backend, mock_client


class TestBlobName:
    """Tests for _blob_name prefix handling."""

    def test_prefix_appended(self):
        backend, _ = _make_backend("user1")
        assert backend._blob_name("emails/test.md") == "user1/emails/test.md"

    def test_prefix_trailing_slash_stripped(self):
        backend, _ = _make_backend("user1/")
        assert backend._blob_name("emails/test.md") == "user1/emails/test.md"

    def test_empty_prefix(self):
        backend, _ = _make_backend("")
        assert backend._blob_name("emails/test.md") == "emails/test.md"


class TestWriteFile:
    def test_uploads_utf8_with_overwrite(self):
        backend, mock_client = _make_backend("pfx")
        backend.write_file("test.md", "# Hello")

        mock_client.upload_blob.assert_called_once_with(
            "pfx/test.md",
            b"# Hello",
            overwrite=True,
        )


class TestReadFile:
    def test_decodes_utf8(self):
        backend, mock_client = _make_backend("pfx")

        blob_client = MagicMock()
        mock_client.get_blob_client.return_value = blob_client
        blob_client.download_blob.return_value.readall.return_value = b"# Hello"

        result = backend.read_file("test.md")

        assert result == "# Hello"
        mock_client.get_blob_client.assert_called_once_with("pfx/test.md")


class TestFileExists:
    def test_returns_true_when_exists(self):
        backend, mock_client = _make_backend("pfx")

        blob_client = MagicMock()
        mock_client.get_blob_client.return_value = blob_client

        assert backend.file_exists("test.md") is True

    def test_returns_false_when_missing(self):
        backend, mock_client = _make_backend("pfx")

        blob_client = MagicMock()
        mock_client.get_blob_client.return_value = blob_client
        blob_client.get_blob_properties.side_effect = ResourceNotFoundError("not found")

        assert backend.file_exists("nonexistent.md") is False


class TestListFiles:
    def test_strips_prefix_from_results(self):
        backend, mock_client = _make_backend("pfx")

        blob1 = MagicMock()
        blob1.name = "pfx/emails/a.md"
        blob2 = MagicMock()
        blob2.name = "pfx/emails/b.md"
        mock_client.list_blobs.return_value = [blob1, blob2]

        result = backend.list_files("emails")

        assert result == ["emails/a.md", "emails/b.md"]
        mock_client.list_blobs.assert_called_once_with(name_starts_with="pfx/emails")

    def test_empty_result(self):
        backend, mock_client = _make_backend("pfx")
        mock_client.list_blobs.return_value = []

        assert backend.list_files("nonexistent") == []


class TestDeleteFile:
    def test_deletes_blob(self):
        backend, mock_client = _make_backend("pfx")

        blob_client = MagicMock()
        mock_client.get_blob_client.return_value = blob_client

        backend.delete_file("test.md")

        blob_client.delete_blob.assert_called_once()

    def test_ignores_missing_blob(self):
        backend, mock_client = _make_backend("pfx")

        blob_client = MagicMock()
        mock_client.get_blob_client.return_value = blob_client
        blob_client.delete_blob.side_effect = ResourceNotFoundError("not found")

        backend.delete_file("nonexistent.md")  # Should not raise
