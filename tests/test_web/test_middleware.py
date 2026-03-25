"""Tests for web middleware (access control)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from m365_extract.web.exceptions import AccessDeniedError
from m365_extract.web.middleware import require_admin, require_same_user


class TestRequireSameUser:
    def test_allows_matching_user(self):
        request = MagicMock()
        request.session = {"user_id": "user-123"}
        require_same_user(request, "user-123")

    def test_rejects_different_user(self):
        request = MagicMock()
        request.session = {"user_id": "user-123"}
        with pytest.raises(AccessDeniedError, match="cannot access"):
            require_same_user(request, "user-456")

    def test_rejects_unauthenticated(self):
        request = MagicMock()
        request.session = {}
        with pytest.raises(AccessDeniedError, match="Authentication required"):
            require_same_user(request, "user-123")

    def test_rejects_none_session_user(self):
        request = MagicMock()
        request.session = {"user_id": None}
        with pytest.raises(AccessDeniedError, match="Authentication required"):
            require_same_user(request, "user-123")


class TestRequireAdmin:
    def _make_request(self, header_value: str | None) -> MagicMock:
        request = MagicMock()
        request.headers = {"X-Admin-Secret": header_value} if header_value is not None else {}
        return request

    def _make_config(self) -> MagicMock:
        config = MagicMock()
        config.web.admin_secret = "correct-secret"
        return config

    def test_allows_valid_secret(self):
        require_admin(self._make_request("correct-secret"), config=self._make_config())

    def test_rejects_missing_header(self):
        with pytest.raises(AccessDeniedError, match="Admin authentication required"):
            require_admin(self._make_request(None), config=self._make_config())

    def test_rejects_wrong_secret(self):
        with pytest.raises(AccessDeniedError, match="Invalid admin secret"):
            require_admin(self._make_request("wrong-secret"), config=self._make_config())
