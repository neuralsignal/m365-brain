"""Shared test fixtures for web module tests.

web_config and full_web_config are inherited from tests/conftest.py.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from m365_extract.web.app import create_app
from m365_extract.web.dependencies import get_config, get_token_store, get_user_manager


@pytest.fixture()
def mock_user_manager():
    return MagicMock()


@pytest.fixture()
def mock_token_store():
    return MagicMock()


@pytest.fixture()
def app_with_overrides(full_web_config, mock_user_manager, mock_token_store):
    """Create a FastAPI app with dependency overrides for testing."""
    app = create_app(full_web_config)
    app.dependency_overrides[get_config] = lambda: full_web_config
    app.dependency_overrides[get_user_manager] = lambda: mock_user_manager
    app.dependency_overrides[get_token_store] = lambda: mock_token_store
    return app


@pytest.fixture()
def client(app_with_overrides):
    return TestClient(app_with_overrides)
