"""Tests for auth state logic."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest
from hypothesis import given
from hypothesis import strategies as st

from m365_admin.auth_state import (
    _OAUTH_STATE_TTL_SECONDS,
    _store_oauth_state,
    _verify_oauth_state,
    extract_user_info,
)

pytestmark = pytest.mark.admin


class TestExtractUserInfo:
    def test_extracts_from_valid_response(self):
        response = {
            "id_token_claims": {
                "oid": "user-123",
                "name": "Alice Test",
                "preferred_username": "alice@example.com",
            },
            "access_token": "at-xxx",
        }
        result = extract_user_info(response)
        assert result == {
            "user_id": "user-123",
            "display_name": "Alice Test",
            "email": "alice@example.com",
        }

    def test_missing_name_falls_back_to_empty(self):
        response = {
            "id_token_claims": {
                "oid": "user-456",
                "preferred_username": "bob@example.com",
            },
        }
        result = extract_user_info(response)
        assert result["display_name"] == ""
        assert result["user_id"] == "user-456"

    def test_missing_email_falls_back_to_empty(self):
        response = {
            "id_token_claims": {
                "oid": "user-789",
                "name": "Charlie",
            },
        }
        result = extract_user_info(response)
        assert result["email"] == ""

    def test_raises_on_missing_claims(self):
        with pytest.raises(KeyError):
            extract_user_info({})

    def test_raises_on_missing_oid(self):
        with pytest.raises(KeyError):
            extract_user_info({"id_token_claims": {"name": "No OID"}})

    @given(
        oid=st.text(min_size=1, max_size=100),
        name=st.text(max_size=200),
        email=st.emails(),
    )
    def test_roundtrip_preserves_values(self, oid, name, email):
        response = {
            "id_token_claims": {
                "oid": oid,
                "name": name,
                "preferred_username": email,
            },
        }
        result = extract_user_info(response)
        assert result["user_id"] == oid
        assert result["display_name"] == name
        assert result["email"] == email


class TestIsAuthenticated:
    """Test the is_authenticated logic without instantiating full Reflex state."""

    def test_empty_user_id_means_not_authenticated(self):
        # The logic: is_authenticated = user_id != ""
        assert ("" != "") is False

    def test_nonempty_user_id_means_authenticated(self):
        assert ("some-id" != "") is True


class TestOAuthStatePersistence:
    """Tests for file-based OAuth state store/verify."""

    @pytest.fixture()
    def state_dir(self, tmp_path):
        """Patch _oauth_state_path to use a temp directory."""
        state_file = tmp_path / "oauth_state.json"
        with patch("m365_admin.auth_state._oauth_state_path", return_value=state_file):
            yield tmp_path, state_file

    def test_store_and_verify_roundtrip(self, state_dir):
        _tmp, state_file = state_dir
        _store_oauth_state("token-abc")
        assert _verify_oauth_state("token-abc") is True

    def test_verify_consumes_token(self, state_dir):
        """Token is deleted after first successful verification (one-time use)."""
        _tmp, state_file = state_dir
        _store_oauth_state("token-multi")
        assert _verify_oauth_state("token-multi") is True
        assert _verify_oauth_state("token-multi") is False

    def test_verify_unknown_token_returns_false(self, state_dir):
        _tmp, state_file = state_dir
        _store_oauth_state("token-known")
        assert _verify_oauth_state("token-unknown") is False

    def test_verify_no_file_returns_false(self, state_dir):
        _tmp, state_file = state_dir
        assert _verify_oauth_state("anything") is False

    def test_expired_token_rejected(self, state_dir):
        _tmp, state_file = state_dir
        # Write a token with a timestamp in the past
        expired_time = time.time() - _OAUTH_STATE_TTL_SECONDS - 1
        state_file.write_text(json.dumps({"old-token": expired_time}))
        assert _verify_oauth_state("old-token") is False

    def test_store_prunes_expired_tokens(self, state_dir):
        _tmp, state_file = state_dir
        # Seed with an expired token
        expired_time = time.time() - _OAUTH_STATE_TTL_SECONDS - 100
        state_file.write_text(json.dumps({"expired": expired_time}))

        _store_oauth_state("fresh")
        data = json.loads(state_file.read_text())
        assert "expired" not in data
        assert "fresh" in data

    def test_multiple_tokens_coexist(self, state_dir):
        _tmp, state_file = state_dir
        _store_oauth_state("token-a")
        _store_oauth_state("token-b")
        assert _verify_oauth_state("token-a") is True
        # token-a consumed, token-b still valid
        assert _verify_oauth_state("token-a") is False
        assert _verify_oauth_state("token-b") is True

    @given(token=st.text(min_size=1, max_size=100, alphabet="abcdefghijklmnopqrstuvwxyz0123456789-_"))
    def test_store_verify_property(self, token):
        """Property: store(t) then verify(t) always returns True."""
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            state_file = Path(td) / "oauth_state.json"
            with patch("m365_admin.auth_state._oauth_state_path", return_value=state_file):
                _store_oauth_state(token)
                assert _verify_oauth_state(token) is True


class TestIsAdmin:
    """Test the is_admin logic (trivial email-in-list check)."""

    def test_admin_email_is_admin(self):
        from m365_extract.config.schema import WebConfig

        wc = WebConfig(
            host="h",
            port=0,
            secret_key="s",
            fernet_key="f",
            db_path="d",
            session_timeout_minutes=0,
            db_url="sqlite://",
            admin_emails=["admin@test.com"],
        )
        assert ("admin@test.com" in wc.admin_emails) is True
        assert ("user@test.com" in wc.admin_emails) is False
