"""Shared test fixtures for m365-extract."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

# Skip test_admin/ and test_worker.py when sqlmodel/reflex is not installed (default env)
try:
    import reflex  # noqa: F401
except ImportError:
    collect_ignore_glob = ["test_admin/test_*.py"]

from m365_extract.config import (
    AuthConfig,
    CalendarExtractorConfig,
    Config,
    ContactsExtractorConfig,
    ConvertersConfig,
    DirectoryExtractorConfig,
    EmailExtractorConfig,
    ExtractionConfig,
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
    WorkerConfig,
)
from m365_extract.storage.local import LocalBackend

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture()
def fixtures_dir():
    return FIXTURES_DIR


def load_fixture(name: str) -> dict:
    """Load a JSON fixture file."""
    return json.loads((FIXTURES_DIR / name).read_text())


@pytest.fixture()
def email_fixture():
    return load_fixture("email_response.json")


@pytest.fixture()
def calendar_fixture():
    return load_fixture("calendar_response.json")


@pytest.fixture()
def teams_chat_fixture():
    return load_fixture("teams_chat_response.json")


@pytest.fixture()
def teams_messages_fixture():
    return load_fixture("teams_messages_response.json")


@pytest.fixture()
def local_storage(tmp_path):
    return LocalBackend(str(tmp_path / "vault"))


@pytest.fixture()
def graph_config():
    return GraphConfig(
        max_retries=3,
        backoff_base_ms=100,
        timeout_seconds=10,
        max_pages=10,
        max_retry_after_seconds=300.0,
        error_message_max_length=200,
    )


@pytest.fixture()
def email_config():
    return EmailExtractorConfig(
        enabled=True,
        poll_interval_minutes=3,
        folders=["Inbox"],
        lookback_days=30,
        max_items_per_sync=100,
    )


@pytest.fixture()
def calendar_config():
    return CalendarExtractorConfig(
        enabled=True,
        poll_interval_minutes=60,
        lookback_days=30,
        forward_days=90,
    )


@pytest.fixture()
def teams_chats_config():
    return TeamsChatsExtractorConfig(
        enabled=True,
        poll_interval_minutes=5,
        max_messages_per_chat=200,
    )


@pytest.fixture()
def teams_channels_config():
    return TeamsChannelsExtractorConfig(
        enabled=True,
        poll_interval_minutes=5,
    )


@pytest.fixture()
def full_config(tmp_path):
    return Config(
        auth=AuthConfig(
            client_id="test-client-id",
            tenant_id="test-tenant-id",
            scopes=["User.Read", "Mail.Read"],
            token_cache_path=str(tmp_path / "token_cache.json"),
            client_secret=None,
        ),
        service=ServiceConfig(
            mode="cli",
            log_level="DEBUG",
            json_logs=False,
            continuous_poll_seconds=30,
            max_consecutive_auth_failures=5,
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
            max_retry_after_seconds=300.0,
            error_message_max_length=200,
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
                forward_days=90,
            ),
            teams_chats=TeamsChatsExtractorConfig(
                enabled=True,
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
        converters=ConvertersConfig(
            backends={"pdf": "markitdown", "docx": "markitdown", "default": "native"},
            extraction=ExtractionConfig(timeout_seconds=30, max_file_size_mb=100, xlsx_max_rows_per_sheet=500),
            slug_max_length=80,
            hash_length=6,
        ),
        web=None,
        worker=WorkerConfig(max_concurrent_jobs=2, poll_interval_seconds=5),
    )


@pytest.fixture()
def web_config(tmp_path):
    from cryptography.fernet import Fernet

    fernet_key = Fernet.generate_key().decode()
    return WebConfig(
        host="127.0.0.1",
        port=8000,
        secret_key="test-secret-key-for-sessions",
        fernet_key=fernet_key,
        db_path=str(tmp_path / "web.db"),
        session_timeout_minutes=60,
        db_url="sqlite://",
        admin_emails=["admin@example.com"],
    )


@pytest.fixture()
def full_web_config(tmp_path, web_config):
    """Complete Config with all extractors for web mode testing."""
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
            json_logs=False,
            continuous_poll_seconds=30,
            max_consecutive_auth_failures=5,
        ),
        storage=StorageConfig(
            backend="local",
            local=LocalStorageConfig(base_path=str(tmp_path / "vault")),
            azure_blob=None,
        ),
        graph=GraphConfig(
            max_retries=2,
            backoff_base_ms=100,
            timeout_seconds=5,
            max_pages=10,
            max_retry_after_seconds=300.0,
            error_message_max_length=200,
        ),
        state=StateConfig(state_file_path=str(tmp_path / "sync_state.json")),
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
                forward_days=90,
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
        converters=ConvertersConfig(
            backends={"pdf": "markitdown", "docx": "markitdown", "default": "native"},
            extraction=ExtractionConfig(timeout_seconds=30, max_file_size_mb=100, xlsx_max_rows_per_sheet=500),
            slug_max_length=80,
            hash_length=6,
        ),
        web=web_config,
        worker=WorkerConfig(max_concurrent_jobs=2, poll_interval_seconds=5),
    )
