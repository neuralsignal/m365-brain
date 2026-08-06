"""Tests for storage factory create_storage(), and for key addressing.

`resolve_key` / `storage_key` are the boundary where a storage-relative key
becomes something a caller can act on and back again. They are a pair, so the
properties are stated as a round trip rather than as two tables of examples
that could drift.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from hypothesis import given
from hypothesis import strategies as st

from m365_brain.config import AzureBlobStorageConfig, LocalStorageConfig, StorageConfig
from m365_brain.config.errors import ConfigError
from m365_brain.storage import create_storage, create_user_storage, local_base_path, resolve_key, storage_key
from m365_brain.storage.local import LocalBackend

AZURITE_CONNECTION_STRING = (
    "DefaultEndpointsProtocol=http;AccountName=devstoreaccount1;"
    "AccountKey=Eby8vdM02xNOcqFlqUwJPLlmEtlCDXJ1OUzFT50uSRZ6IFsuFq2UVErCz4I6tq/K1SZFPTOtr/KBHBeksoGMGw==;"
    "BlobEndpoint=http://127.0.0.1:10000/devstoreaccount1;"
)
"""Azurite's published emulator credentials. Not a secret and not anybody's."""

LOCAL_VAULT = StorageConfig(
    backend="local",
    local=LocalStorageConfig(base_path="/srv/vault"),
    azure_blob=None,
)

BLOB_VAULT = StorageConfig(
    backend="azure_blob",
    local=None,
    azure_blob=AzureBlobStorageConfig(
        connection_string=AZURITE_CONNECTION_STRING,
        container_name="vault",
        prefix="tenant/",
    ),
)

# One key's worth of segments. Separators are excluded because a key is built
# from them, not around them -- `vault/paths.py` owns that and rejects the rest.
KEYS = st.lists(
    st.text(
        alphabet=st.characters(blacklist_characters="/\\", blacklist_categories=("Cs", "Cc")),
        min_size=1,
        max_size=12,
    ),
    min_size=1,
    max_size=4,
).map("/".join)


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
            from m365_brain.storage.azure_blob import AzureBlobBackend

            backend = create_storage(config)
            assert isinstance(backend, AzureBlobBackend)

    def test_unknown_backend_crashes(self):
        config = StorageConfig(
            backend="s3",
            local=None,
            azure_blob=None,
        )
        with pytest.raises(ConfigError):
            create_storage(config)

    def test_local_backend_missing_config_crashes(self):
        config = StorageConfig(
            backend="local",
            local=None,
            azure_blob=None,
        )
        with pytest.raises(ConfigError):
            create_storage(config)

    def test_azure_blob_missing_config_crashes(self):
        config = StorageConfig(
            backend="azure_blob",
            local=None,
            azure_blob=None,
        )
        with pytest.raises(ConfigError):
            create_storage(config)


class TestCreateUserStorage:
    def test_local_appends_user_id_to_base_path(self, tmp_path):
        config = StorageConfig(
            backend="local",
            local=LocalStorageConfig(base_path=str(tmp_path / "vault")),
            azure_blob=None,
        )
        uid = "a1b2c3d4-0001-4000-8000-000000000001"
        backend = create_user_storage(config, uid)
        assert isinstance(backend, LocalBackend)
        assert str(backend._base).endswith(uid)

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
        uid = "a1b2c3d4-0002-4000-8000-000000000002"
        with patch("azure.storage.blob.ContainerClient"):
            from m365_brain.storage.azure_blob import AzureBlobBackend

            backend = create_user_storage(config, uid)
            assert isinstance(backend, AzureBlobBackend)
            assert backend._prefix == f"dev/{uid}/"

    def test_local_missing_config_crashes(self):
        config = StorageConfig(
            backend="local",
            local=None,
            azure_blob=None,
        )
        with pytest.raises(ConfigError):
            create_user_storage(config, "a1b2c3d4-0001-4000-8000-000000000001")

    def test_azure_blob_missing_config_crashes(self):
        config = StorageConfig(
            backend="azure_blob",
            local=None,
            azure_blob=None,
        )
        with pytest.raises(ConfigError):
            create_user_storage(config, "a1b2c3d4-0001-4000-8000-000000000001")

    def test_unknown_backend_crashes(self):
        config = StorageConfig(
            backend="s3",
            local=None,
            azure_blob=None,
        )
        with pytest.raises(ConfigError):
            create_user_storage(config, "a1b2c3d4-0001-4000-8000-000000000001")

    def test_rejects_non_uuid_user_id(self, tmp_path):
        config = StorageConfig(
            backend="local",
            local=LocalStorageConfig(base_path=str(tmp_path / "vault")),
            azure_blob=None,
        )
        with pytest.raises(ConfigError, match="Invalid user_id format"):
            create_user_storage(config, "../traversal")


class TestAddressing:
    """A key leaving the process gains its base; the same string coming back loses it."""

    @pytest.mark.parametrize("config", [LOCAL_VAULT, BLOB_VAULT], ids=["local", "azure_blob"])
    @given(key=KEYS)
    def test_a_key_survives_the_round_trip(self, config, key):
        assert storage_key(config, resolve_key(config, key)) == key

    @pytest.mark.parametrize("config", [LOCAL_VAULT, BLOB_VAULT], ids=["local", "azure_blob"])
    @given(key=KEYS)
    def test_resolving_twice_is_resolving_once(self, config, key):
        """`emit` resolves the payload after a call site already resolved a line.

        Without idempotency that second pass would prepend a second base, so
        the human and JSON forms of one row would disagree.
        """
        once = resolve_key(config, key)
        assert resolve_key(config, once) == once

    @given(key=KEYS)
    def test_a_local_address_is_an_absolute_filesystem_path(self, key):
        assert Path(resolve_key(LOCAL_VAULT, key)).is_absolute()

    def test_a_local_key_hangs_off_the_configured_base(self):
        assert resolve_key(LOCAL_VAULT, "inbox/emails/note/index.md") == "/srv/vault/inbox/emails/note/index.md"

    def test_a_blob_key_becomes_a_url_under_the_container_and_prefix(self):
        address = resolve_key(BLOB_VAULT, "inbox/emails/note/index.md")
        assert address.startswith("http://127.0.0.1:10000/devstoreaccount1/vault/")
        assert address.endswith("/tenant/inbox/emails/note/index.md")

    @pytest.mark.parametrize("config", [LOCAL_VAULT, BLOB_VAULT], ids=["local", "azure_blob"])
    def test_an_address_from_another_vault_comes_back_unchanged(self, config):
        """Better a key that matches nothing than a substring guess at the wrong row."""
        assert storage_key(config, "/somewhere/else/report.pdf") == "/somewhere/else/report.pdf"

    def test_a_relative_address_is_already_a_key(self):
        assert storage_key(LOCAL_VAULT, "inbox/emails/note/index.md") == "inbox/emails/note/index.md"

    def test_a_blob_vault_says_why_it_cannot_hand_back_a_file(self):
        with pytest.raises(ConfigError, match="read_bytes"):
            local_base_path(BLOB_VAULT)
