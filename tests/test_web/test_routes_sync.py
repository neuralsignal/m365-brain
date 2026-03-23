"""Tests for sync endpoints."""

from __future__ import annotations

from unittest.mock import patch

from m365_extract.user_manager import UserRecord


class TestTriggerSync:
    @patch("m365_extract.web.routes_sync.make_web_token_provider")
    @patch("m365_extract.web.routes_sync._run_extractors")
    @patch("m365_extract.web.routes_sync.create_storage")
    @patch("m365_extract.web.routes_sync.SyncState")
    def test_trigger_sync(self, mock_state_cls, mock_storage, mock_run, mock_provider, client, mock_user_manager):
        mock_user_manager.get_user.return_value = UserRecord(
            user_id="u1", display_name="Test", email="t@x.com", enabled=True, created_at="2026-01-01"
        )

        response = client.post("/sync/u1")

        assert response.status_code == 200
        assert response.json()["status"] == "completed"
        mock_run.assert_called_once()

    def test_sync_nonexistent_user_404(self, client, mock_user_manager):
        mock_user_manager.get_user.return_value = None

        response = client.post("/sync/nonexistent")

        assert response.status_code == 404

    @patch("m365_extract.web.routes_sync.make_web_token_provider")
    @patch("m365_extract.web.routes_sync._run_extractors")
    @patch("m365_extract.web.routes_sync.create_storage")
    @patch("m365_extract.web.routes_sync.SyncState")
    def test_sync_status(self, mock_state_cls, mock_storage, mock_run, mock_provider, client, mock_user_manager):
        mock_user_manager.get_user.return_value = UserRecord(
            user_id="u1", display_name="Test", email="t@x.com", enabled=True, created_at="2026-01-01"
        )

        # Trigger a sync first to set last_sync
        client.post("/sync/u1")

        response = client.get("/sync/u1/status")

        assert response.status_code == 200
        data = response.json()
        assert data["user_id"] == "u1"
        assert data["last_sync"] is not None

    def test_sync_status_nonexistent_user_404(self, client, mock_user_manager):
        mock_user_manager.get_user.return_value = None

        response = client.get("/sync/nonexistent/status")

        assert response.status_code == 404

    @patch("m365_extract.web.routes_sync.make_web_token_provider")
    @patch("m365_extract.web.routes_sync.create_storage")
    @patch("m365_extract.web.routes_sync.SyncState")
    def test_sync_status_no_prior_sync(self, mock_state_cls, mock_storage, mock_provider, client, mock_user_manager):
        mock_user_manager.get_user.return_value = UserRecord(
            user_id="u-new", display_name="New", email="n@x.com", enabled=True, created_at="2026-01-01"
        )

        response = client.get("/sync/u-new/status")

        assert response.status_code == 200
        assert response.json()["last_sync"] is None
