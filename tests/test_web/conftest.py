"""Shared test fixtures for web module tests."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from m365_extract.config import (
    AuthConfig,
    CalendarExtractorConfig,
    Config,
    ContactsExtractorConfig,
    DirectoryExtractorConfig,
    EmailExtractorConfig,
    ExtractorsConfig,
    GraphConfig,
    LocalStorageConfig,
    OneDriveExtractorConfig,
    ServiceConfig,
    SharePointExtractorConfig,
    StateConfig,
    StorageConfig,
    TeamsChannelsExtractorConfig,
    TeamsChatsExtractorConfig,
    WebConfig,
)
from m365_extract.web.app import create_app
from m365_extract.web.dependencies import get_config, get_token_store, get_user_manager


@pytest.fixture()
def web_config(tmp_path):
    fernet_key = Fernet.generate_key().decode()
    return WebConfig(
        host="127.0.0.1",
        port=8000,
        secret_key="test-secret-key-for-sessions",
        fernet_key=fernet_key,
        db_path=str(tmp_path / "web.db"),
        session_timeout_minutes=60,
        admin_secret="test-admin-secret",
    )


@pytest.fixture()
def full_web_config(tmp_path, web_config):
    return Config(
        auth=AuthConfig(
            client_id="test-client-id",
            tenant_id="test-tenant-id",
            scopes=["User.Read", "Mail.Read"],
            token_cache_path=str(tmp_path / "token_cache.json"),
            client_secret="test-client-secret",
        ),
        service=ServiceConfig(
            mode="web",
            log_level="DEBUG",
        ),
        storage=StorageConfig(
            backend="local",
            local=LocalStorageConfig(
                base_path=str(tmp_path / "vault"),
            ),
            azure_blob=None,
        ),
        graph=GraphConfig(
            max_retries=2,
            backoff_base_ms=100,
            timeout_seconds=5,
            max_pages=10,
        ),
        state=StateConfig(
            state_file_path=str(tmp_path / "sync_state.json"),
        ),
        extractors=ExtractorsConfig(
            email=EmailExtractorConfig(
                enabled=True,
                poll_interval_minutes=3,
                folders=["Inbox"],
                lookback_days=30,
                max_items_per_sync=100,
            ),
            calendar=CalendarExtractorConfig(
                enabled=True,
                poll_interval_minutes=60,
                lookback_days=30,
            ),
            teams_chats=TeamsChatsExtractorConfig(
                enabled=False,
                poll_interval_minutes=5,
                max_messages_per_chat=200,
            ),
            teams_channels=TeamsChannelsExtractorConfig(
                enabled=False,
                poll_interval_minutes=5,
            ),
            onedrive=OneDriveExtractorConfig(
                enabled=False,
                poll_interval_minutes=120,
                eager_convert_patterns=[],
                convertible_extensions=[".docx", ".pdf"],
                max_file_size_mb=100,
            ),
            sharepoint=SharePointExtractorConfig(
                enabled=False,
                poll_interval_minutes=240,
                eager_convert_patterns=[],
                convertible_extensions=[".docx", ".pdf"],
                max_file_size_mb=100,
            ),
            contacts=ContactsExtractorConfig(
                enabled=False,
                poll_interval_minutes=1440,
                max_items_per_sync=500,
                include_contact_folders=False,
            ),
            directory=DirectoryExtractorConfig(
                enabled=False,
                poll_interval_minutes=10080,
                include_manager_chain=True,
                include_direct_reports=True,
                only_active_users=True,
            ),
        ),
        converters={
            "backends": {"pdf": "markitdown", "docx": "markitdown", "default": "native"},
            "extraction": {"timeout_seconds": 30, "max_file_size_mb": 100, "xlsx_max_rows_per_sheet": 500},
        },
        web=web_config,
    )


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
