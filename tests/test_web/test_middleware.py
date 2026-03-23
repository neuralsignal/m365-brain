"""Tests for web middleware (access control)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from m365_extract.web.exceptions import AccessDeniedError
from m365_extract.web.middleware import require_same_user


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
