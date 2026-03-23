"""Tests for health endpoint."""

from __future__ import annotations

from m365_extract.user_manager import UserRecord


class TestHealth:
    def test_health_returns_ok(self, client, mock_user_manager):
        mock_user_manager.list_users.return_value = []

        response = client.get("/health")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"

    def test_health_counts_users(self, client, mock_user_manager):
        mock_user_manager.list_users.return_value = [
            UserRecord(user_id="u1", display_name="A", email="a@x.com", enabled=True, created_at="2026-01-01"),
            UserRecord(user_id="u2", display_name="B", email="b@x.com", enabled=True, created_at="2026-01-01"),
        ]

        response = client.get("/health")

        assert response.json()["users"] == 2

    def test_health_includes_version(self, client, mock_user_manager):
        mock_user_manager.list_users.return_value = []

        response = client.get("/health")

        assert "version" in response.json()
        assert isinstance(response.json()["version"], str)
