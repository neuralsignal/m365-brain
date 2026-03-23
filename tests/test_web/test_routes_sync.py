"""Tests for sync endpoints."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from m365_extract.config import AzureBlobStorageConfig, StorageConfig
from m365_extract.user_manager import UserRecord
from m365_extract.web.exceptions import AccessDeniedError


class TestTriggerSync:
    @patch("m365_extract.web.routes_sync.make_web_token_provider")
    @patch("m365_extract.web.routes_sync.run_extractors")
    @patch("m365_extract.web.routes_sync.create_storage")
    @patch("m365_extract.web.routes_sync.SyncState")
    def test_trigger_sync(self, mock_state_cls, mock_storage, mock_run, mock_provider, client, mock_user_manager):
        mock_user_manager.get_user.return_value = UserRecord(
            user_id="u1", display_name="Test", email="t@x.com", enabled=True, created_at="2026-01-01"
        )

        # Set up session so access control passes
        with client:
            client.cookies.set("session", "")
            # Override the session middleware for testing
            with patch("m365_extract.web.routes_sync.require_same_user"):
                response = client.post("/sync/u1")

        assert response.status_code == 200
        assert response.json()["status"] == "completed"
        mock_run.assert_called_once()

    def test_sync_nonexistent_user_404(self, client, mock_user_manager):
        mock_user_manager.get_user.return_value = None

        with patch("m365_extract.web.routes_sync.require_same_user"):
            response = client.post("/sync/nonexistent")

        assert response.status_code == 404

    @patch("m365_extract.web.routes_sync.make_web_token_provider")
    @patch("m365_extract.web.routes_sync.run_extractors")
    @patch("m365_extract.web.routes_sync.create_storage")
    @patch("m365_extract.web.routes_sync.SyncState")
    def test_sync_status(self, mock_state_cls, mock_storage, mock_run, mock_provider, client, mock_user_manager):
        mock_user_manager.get_user.return_value = UserRecord(
            user_id="u1", display_name="Test", email="t@x.com", enabled=True, created_at="2026-01-01"
        )

        with patch("m365_extract.web.routes_sync.require_same_user"):
            # Trigger a sync first to set last_sync
            client.post("/sync/u1")

            response = client.get("/sync/u1/status")

        assert response.status_code == 200
        data = response.json()
        assert data["user_id"] == "u1"
        assert data["last_sync"] is not None

    def test_sync_status_nonexistent_user_404(self, client, mock_user_manager):
        mock_user_manager.get_user.return_value = None

        with patch("m365_extract.web.routes_sync.require_same_user"):
            response = client.get("/sync/nonexistent/status")

        assert response.status_code == 404

    @patch("m365_extract.web.routes_sync.make_web_token_provider")
    @patch("m365_extract.web.routes_sync.create_storage")
    @patch("m365_extract.web.routes_sync.SyncState")
    def test_sync_status_no_prior_sync(self, mock_state_cls, mock_storage, mock_provider, client, mock_user_manager):
        mock_user_manager.get_user.return_value = UserRecord(
            user_id="u-new", display_name="New", email="n@x.com", enabled=True, created_at="2026-01-01"
        )

        with patch("m365_extract.web.routes_sync.require_same_user"):
            response = client.get("/sync/u-new/status")

        assert response.status_code == 200
        assert response.json()["last_sync"] is None


class TestUserScopedStorage:
    def test_user_scoped_storage_appends_user_id(self, full_web_config):
        """Verify _user_scoped_storage creates a path with user_id prefix."""
        from m365_extract.web.routes_sync import _user_scoped_storage

        with patch("m365_extract.web.routes_sync.create_storage") as mock_create:
            mock_create.return_value = MagicMock()
            _user_scoped_storage(full_web_config, "user-xyz")

        called_config = mock_create.call_args[0][0]
        assert called_config.local is not None
        assert called_config.local.base_path.endswith("/user-xyz")

    def test_azure_blob_storage_unchanged(self):
        """If using azure_blob backend, user-scoped storage doesn't modify blob config."""
        from m365_extract.web.routes_sync import _user_scoped_storage

        blob_config = AzureBlobStorageConfig(
            connection_string="DefaultEndpointsProtocol=https;AccountName=test",
            container_name="data",
            prefix="vault",
        )
        storage_config = StorageConfig(backend="azure_blob", local=None, azure_blob=blob_config)

        mock_config = MagicMock()
        mock_config.storage = storage_config

        with patch("m365_extract.web.routes_sync.create_storage") as mock_create:
            mock_create.return_value = MagicMock()
            _user_scoped_storage(mock_config, "user-xyz")

        called_config = mock_create.call_args[0][0]
        assert called_config.local is None
        assert called_config.azure_blob is blob_config


class TestAccessControl:
    def test_trigger_sync_returns_403_for_wrong_user(self, client, mock_user_manager):
        """POST /sync/{user_id} should return 403 if session user != user_id."""
        mock_user_manager.get_user.return_value = UserRecord(
            user_id="u1", display_name="Test", email="t@x.com", enabled=True, created_at="2026-01-01"
        )

        # require_same_user raises AccessDeniedError — test via the exception handler
        with patch(
            "m365_extract.web.routes_sync.require_same_user",
            side_effect=AccessDeniedError("cannot access"),
        ):
            response = client.post("/sync/u1")

        assert response.status_code == 403

    def test_status_returns_403_for_wrong_user(self, client, mock_user_manager):
        """GET /sync/{user_id}/status should return 403 if session user != user_id."""
        mock_user_manager.get_user.return_value = UserRecord(
            user_id="u1", display_name="Test", email="t@x.com", enabled=True, created_at="2026-01-01"
        )

        with patch(
            "m365_extract.web.routes_sync.require_same_user",
            side_effect=AccessDeniedError("cannot access"),
        ):
            response = client.get("/sync/u1/status")

        assert response.status_code == 403
