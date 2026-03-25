"""Tests for admin endpoints."""

from __future__ import annotations

import pytest

from m365_extract.user_manager import UserRecord

ADMIN_HEADERS = {"X-Admin-Secret": "test-admin-secret"}


class TestAdminAuth:
    """All admin endpoints require a valid admin secret header."""

    @pytest.mark.parametrize(
        "method,path",
        [
            ("GET", "/admin/users"),
            ("POST", "/admin/users/u1/enable"),
            ("POST", "/admin/users/u1/disable"),
            ("DELETE", "/admin/users/u1"),
        ],
    )
    def test_missing_header_returns_403(self, client, method, path):
        response = client.request(method, path)
        assert response.status_code == 403

    @pytest.mark.parametrize(
        "method,path",
        [
            ("GET", "/admin/users"),
            ("POST", "/admin/users/u1/enable"),
            ("POST", "/admin/users/u1/disable"),
            ("DELETE", "/admin/users/u1"),
        ],
    )
    def test_wrong_secret_returns_403(self, client, method, path):
        response = client.request(method, path, headers={"X-Admin-Secret": "wrong"})
        assert response.status_code == 403


class TestListUsers:
    def test_list_users_empty(self, client, mock_user_manager):
        mock_user_manager.list_users.return_value = []

        response = client.get("/admin/users", headers=ADMIN_HEADERS)

        assert response.status_code == 200
        assert response.json()["users"] == []

    def test_list_users_with_data(self, client, mock_user_manager):
        mock_user_manager.list_users.return_value = [
            UserRecord(user_id="u1", display_name="Alice", email="a@x.com", enabled=True, created_at="2026-01-01"),
            UserRecord(user_id="u2", display_name="Bob", email="b@x.com", enabled=False, created_at="2026-01-02"),
        ]

        response = client.get("/admin/users", headers=ADMIN_HEADERS)

        data = response.json()
        assert len(data["users"]) == 2
        assert data["users"][0]["user_id"] == "u1"
        assert data["users"][0]["enabled"] is True
        assert data["users"][1]["user_id"] == "u2"
        assert data["users"][1]["enabled"] is False


class TestEnableDisable:
    def test_enable_user(self, client, mock_user_manager):
        response = client.post("/admin/users/u1/enable", headers=ADMIN_HEADERS)

        assert response.status_code == 200
        assert response.json()["status"] == "enabled"
        mock_user_manager.set_enabled.assert_called_once_with("u1", enabled=True)

    def test_disable_user(self, client, mock_user_manager):
        response = client.post("/admin/users/u1/disable", headers=ADMIN_HEADERS)

        assert response.status_code == 200
        assert response.json()["status"] == "disabled"
        mock_user_manager.set_enabled.assert_called_once_with("u1", enabled=False)

    def test_enable_nonexistent_404(self, client, mock_user_manager):
        mock_user_manager.set_enabled.side_effect = ValueError("user 'nope' not found")

        response = client.post("/admin/users/nope/enable", headers=ADMIN_HEADERS)

        assert response.status_code == 404


class TestDeleteUser:
    def test_delete_user_removes_tokens(self, client, mock_user_manager, mock_token_store):
        response = client.delete("/admin/users/u1", headers=ADMIN_HEADERS)

        assert response.status_code == 200
        assert response.json()["status"] == "deleted"
        mock_token_store.delete_tokens.assert_called_once_with("u1")
        mock_user_manager.delete_user.assert_called_once_with("u1")
