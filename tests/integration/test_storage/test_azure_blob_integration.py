"""Integration tests for Azure Blob Storage backend against Azurite emulator.

Run Azurite first:
    docker compose --profile azurite up -d

Then:
    pixi run pytest tests/test_storage/test_azure_blob_integration.py -m azurite
"""

from __future__ import annotations

import uuid

import pytest

# Well-known Azurite connection string (not a secret)
AZURITE_CONNECTION_STRING = (
    "DefaultEndpointsProtocol=http;"
    "AccountName=devstoreaccount1;"
    "AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/"
    "K1SZFPTOtr/KBHBeksoGMGw==;"
    "BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;"
)


def _azurite_reachable() -> bool:
    """Quick check if Azurite blob service is listening on localhost:10000."""
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(1)
    try:
        sock.connect(("127.0.0.1", 10000))
        sock.close()
        return True
    except (ConnectionRefusedError, OSError):
        return False


@pytest.fixture()
def azurite_backend():
    """Create an AzureBlobBackend connected to local Azurite with a unique container."""
    if not _azurite_reachable():
        pytest.skip("Azurite not running on localhost:10000")

    from m365_brain.storage.azure_blob import AzureBlobBackend

    container_name = f"test-{uuid.uuid4().hex[:8]}"
    backend = AzureBlobBackend(
        connection_string=AZURITE_CONNECTION_STRING,
        container_name=container_name,
        prefix="test/",
    )
    yield backend
    # Cleanup
    backend._container_client.delete_container()


@pytest.mark.azurite
class TestAzureBlobIntegration:
    def test_write_and_read(self, azurite_backend):
        azurite_backend.write_file("hello.md", "# Hello")
        assert azurite_backend.read_file("hello.md") == "# Hello"

    def test_file_exists(self, azurite_backend):
        assert not azurite_backend.file_exists("nonexistent.md")
        azurite_backend.write_file("test.md", "content")
        assert azurite_backend.file_exists("test.md")

    def test_list_files(self, azurite_backend):
        azurite_backend.write_file("emails/a.md", "a")
        azurite_backend.write_file("emails/b.md", "b")
        azurite_backend.write_file("calendar/c.md", "c")

        email_files = azurite_backend.list_files("emails")
        assert len(email_files) == 2
        assert "emails/a.md" in email_files
        assert "emails/b.md" in email_files

    def test_list_files_empty_prefix(self, azurite_backend):
        assert azurite_backend.list_files("nonexistent") == []

    def test_delete_file(self, azurite_backend):
        azurite_backend.write_file("test.md", "content")
        assert azurite_backend.file_exists("test.md")
        azurite_backend.delete_file("test.md")
        assert not azurite_backend.file_exists("test.md")

    def test_write_overwrites_existing(self, azurite_backend):
        azurite_backend.write_file("test.md", "v1")
        azurite_backend.write_file("test.md", "v2")
        assert azurite_backend.read_file("test.md") == "v2"

    def test_nested_paths(self, azurite_backend):
        azurite_backend.write_file("a/b/c/d/file.md", "deep content")
        assert azurite_backend.read_file("a/b/c/d/file.md") == "deep content"

    def test_unicode_content(self, azurite_backend):
        content = "# Greetings\n\nHallo Welt! Salut le monde! Schweizer Gruezi!"
        azurite_backend.write_file("unicode.md", content)
        assert azurite_backend.read_file("unicode.md") == content

    def test_delete_nonexistent_no_error(self, azurite_backend):
        azurite_backend.delete_file("never-existed.md")  # Should not raise
