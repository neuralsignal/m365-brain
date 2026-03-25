"""Tests for storage factory create_storage()."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from m365_extract.config import AzureBlobStorageConfig, LocalStorageConfig, StorageConfig
from m365_extract.storage import create_storage, create_user_storage
from m365_extract.storage.local import LocalBackend


class TestCreateStorage:
    def test_returns_local_backend(self, tmp_path):
        config = StorageConfig(
            backend="local",
            local=LocalStorageConfig(base_path=str(tmp_path / "vault")),
            azure_blob=None,
        )
        backend = create_storage(config)
        assert isinstance(backend, LocalBackend)

    def test_returns_azure_blob_backend(self):
        config = StorageConfig(
            backend="azure_blob",
            local=None,
            azure_blob=AzureBlobStorageConfig(
                connection_string="DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;",
                container_name="test-container",
                prefix="test/",
            ),
        )
        with patch("azure.storage.blob.ContainerClient"):
            from m365_extract.storage.azure_blob import AzureBlobBackend

            backend = create_storage(config)
            assert isinstance(backend, AzureBlobBackend)

    def test_unknown_backend_crashes(self):
        config = StorageConfig(
            backend="s3",
            local=None,
            azure_blob=None,
        )
        with pytest.raises(SystemExit):
            create_storage(config)

    def test_local_backend_missing_config_crashes(self):
        config = StorageConfig(
            backend="local",
            local=None,
            azure_blob=None,
        )
        with pytest.raises(SystemExit):
            create_storage(config)

    def test_azure_blob_missing_config_crashes(self):
        config = StorageConfig(
            backend="azure_blob",
            local=None,
            azure_blob=None,
        )
        with pytest.raises(SystemExit):
            create_storage(config)


class TestCreateUserStorage:
    def test_local_appends_user_id_to_base_path(self, tmp_path):
        config = StorageConfig(
            backend="local",
            local=LocalStorageConfig(base_path=str(tmp_path / "vault")),
            azure_blob=None,
        )
        backend = create_user_storage(config, "user-abc-123")
        assert isinstance(backend, LocalBackend)
        # The resolved base should end with the user_id
        assert str(backend._base).endswith("user-abc-123")

    def test_azure_blob_appends_user_id_to_prefix(self):
        config = StorageConfig(
            backend="azure_blob",
            local=None,
            azure_blob=AzureBlobStorageConfig(
                connection_string="DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;",
                container_name="test-container",
                prefix="dev/",
            ),
        )
        with patch("azure.storage.blob.ContainerClient"):
            from m365_extract.storage.azure_blob import AzureBlobBackend

            backend = create_user_storage(config, "user-xyz-789")
            assert isinstance(backend, AzureBlobBackend)
            # Prefix should be dev/user-xyz-789/
            assert backend._prefix == "dev/user-xyz-789/"

    def test_unknown_backend_crashes(self):
        config = StorageConfig(
            backend="s3",
            local=None,
            azure_blob=None,
        )
        with pytest.raises(SystemExit):
            create_user_storage(config, "user-1")
