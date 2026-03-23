"""Tests for auth endpoints."""

from __future__ import annotations

from unittest.mock import MagicMock, patch


class TestLogin:
    @patch("m365_extract.web.routes_auth.AuthCodeAuth")
    def test_login_redirects(self, mock_auth_cls, client, full_web_config):
        mock_auth = MagicMock()
        mock_auth_cls.return_value = mock_auth
        mock_auth.get_auth_url.return_value = "https://login.microsoftonline.com/authorize?code=abc"

        response = client.get("/auth/login", follow_redirects=False)

        assert response.status_code == 307
        assert response.headers["location"] == "https://login.microsoftonline.com/authorize?code=abc"


class TestCallback:
    @patch("m365_extract.web.routes_auth.AuthCodeAuth")
    def test_callback_stores_tokens(self, mock_auth_cls, client, mock_token_store, mock_user_manager):
        mock_auth = MagicMock()
        mock_auth_cls.return_value = mock_auth
        mock_auth.acquire_token_by_code.return_value = {
            "access_token": "at-123",
            "refresh_token": "rt-456",
            "expires_in": 3600,
            "id_token_claims": {
                "oid": "user-oid",
                "name": "Test User",
                "preferred_username": "test@example.com",
            },
        }
        mock_user_manager.get_user.return_value = None

        # Set up session with oauth_state
        with client:
            # First, set the session state via login
            mock_auth.get_auth_url.return_value = "https://example.com"
            client.get("/auth/login", follow_redirects=False)

            # Extract the state from the auth URL call
            state = mock_auth.get_auth_url.call_args[1]["state"]

            response = client.get(f"/auth/callback?code=auth-code&state={state}")

        assert response.status_code == 200
        mock_token_store.store_tokens.assert_called_once()
        stored_user_id = mock_token_store.store_tokens.call_args[0][0]
        assert stored_user_id == "user-oid"

    @patch("m365_extract.web.routes_auth.AuthCodeAuth")
    def test_callback_creates_user(self, mock_auth_cls, client, mock_token_store, mock_user_manager):
        mock_auth = MagicMock()
        mock_auth_cls.return_value = mock_auth
        mock_auth.acquire_token_by_code.return_value = {
            "access_token": "at-123",
            "refresh_token": "rt-456",
            "expires_in": 3600,
            "id_token_claims": {
                "oid": "user-oid",
                "name": "Test User",
                "preferred_username": "test@example.com",
            },
        }
        mock_user_manager.get_user.return_value = None

        with client:
            mock_auth.get_auth_url.return_value = "https://example.com"
            client.get("/auth/login", follow_redirects=False)
            state = mock_auth.get_auth_url.call_args[1]["state"]

            client.get(f"/auth/callback?code=auth-code&state={state}")

        mock_user_manager.create_user.assert_called_once_with(
            user_id="user-oid",
            display_name="Test User",
            email="test@example.com",
        )

    @patch("m365_extract.web.routes_auth.AuthCodeAuth")
    def test_callback_rejects_bad_state(self, mock_auth_cls, client):
        mock_auth_cls.return_value = MagicMock()

        response = client.get("/auth/callback?code=auth-code&state=wrong-state")

        assert response.status_code == 400
        assert "Invalid OAuth state" in response.json()["error"]


class TestLogout:
    def test_logout_clears_session(self, client):
        response = client.post("/auth/logout")

        assert response.status_code == 200
        assert response.json()["status"] == "logged_out"
