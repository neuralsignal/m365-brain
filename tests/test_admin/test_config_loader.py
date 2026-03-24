"""Tests for config_loader module."""

from __future__ import annotations

import pytest
import yaml

from m365_admin.config_loader import get_config, get_config_path, reset_config

pytestmark = pytest.mark.admin


@pytest.fixture(autouse=True)
def _clean_config_cache():
    """Reset the config singleton before and after each test."""
    reset_config()
    yield
    reset_config()


def _write_config_yaml(tmp_path, full_web_config):
    """Helper: write a minimal valid config.yaml and return the path."""
    config_data = {
        "auth": {
            "client_id": "test-id",
            "tenant_id": "test-tenant",
            "scopes": ["User.Read"],
            "token_cache_path": str(tmp_path / "cache.json"),
            "client_secret": "test-secret",
        },
        "service": {
            "mode": "web",
            "log_level": "INFO",
            "json_logs": False,
            "continuous_poll_seconds": 30,
            "max_consecutive_auth_failures": 5,
        },
        "storage": {
            "backend": "local",
            "local": {"base_path": str(tmp_path / "vault")},
        },
        "graph": {
            "max_retries": 3,
            "backoff_base_ms": 2000,
            "timeout_seconds": 30,
            "max_pages": 100,
        },
        "state": {"state_file_path": str(tmp_path / "state.json")},
        "extractors": {
            "email": {
                "enabled": False,
                "poll_interval_minutes": 3,
                "folders": ["Inbox"],
                "lookback_days": 30,
                "max_items_per_sync": 100,
            },
            "calendar": {
                "enabled": False,
                "poll_interval_minutes": 60,
                "lookback_days": 30,
                "forward_days": 90,
            },
            "teams_chats": {
                "enabled": False,
                "poll_interval_minutes": 5,
                "max_messages_per_chat": 200,
            },
            "teams_channels": {
                "enabled": False,
                "poll_interval_minutes": 5,
            },
            "onedrive": {
                "enabled": False,
                "poll_interval_minutes": 120,
                "eager_convert_patterns": [],
                "convertible_extensions": [".docx"],
                "max_file_size_mb": 100,
            },
            "sharepoint": {
                "enabled": False,
                "poll_interval_minutes": 240,
                "eager_convert_patterns": [],
                "convertible_extensions": [".docx"],
                "max_file_size_mb": 100,
            },
            "contacts": {
                "enabled": False,
                "poll_interval_minutes": 1440,
                "max_items_per_sync": 500,
                "include_contact_folders": False,
            },
            "directory": {
                "enabled": False,
                "poll_interval_minutes": 10080,
                "include_manager_chain": True,
                "include_direct_reports": True,
                "only_active_users": True,
            },
        },
        "converters": {
            "backends": {"default": "native"},
            "extraction": {
                "timeout_seconds": 30,
                "max_file_size_mb": 100,
                "xlsx_max_rows_per_sheet": 500,
            },
        },
        "web": {
            "host": "127.0.0.1",
            "port": 8000,
            "secret_key": "test-secret",
            "fernet_key": full_web_config.web.fernet_key,
            "db_path": str(tmp_path / "web.db"),
            "session_timeout_minutes": 60,
        },
    }
    config_file = tmp_path / "config.yaml"
    config_file.write_text(yaml.dump(config_data))
    return config_file


class TestGetConfig:
    def test_defaults_to_config_web_yaml(self, monkeypatch):
        """When M365_ADMIN_CONFIG is unset, defaults to config.web.yaml in repo root."""
        monkeypatch.delenv("M365_ADMIN_CONFIG", raising=False)
        # config.web.yaml exists in repo root, so get_config() should succeed
        config = get_config()
        assert config is not None

    def test_loads_valid_config(self, tmp_path, monkeypatch, full_web_config):
        """Write a minimal valid config file and verify it loads."""
        config_file = _write_config_yaml(tmp_path, full_web_config)
        monkeypatch.setenv("M365_ADMIN_CONFIG", str(config_file))
        config = get_config()

        assert config.auth.client_id == "test-id"
        assert config.web is not None
        assert config.web.port == 8000

    def test_caches_result(self, tmp_path, monkeypatch, full_web_config):
        """get_config() returns the same object on second call."""
        config_file = _write_config_yaml(tmp_path, full_web_config)
        monkeypatch.setenv("M365_ADMIN_CONFIG", str(config_file))

        first = get_config()
        second = get_config()
        assert first is second

    def test_env_var_overrides_default(self, tmp_path, monkeypatch, full_web_config):
        """M365_ADMIN_CONFIG env var overrides the default path."""
        config_file = _write_config_yaml(tmp_path, full_web_config)
        monkeypatch.setenv("M365_ADMIN_CONFIG", str(config_file))
        config = get_config()
        assert config.auth.client_id == "test-id"

    def test_stores_config_path(self, tmp_path, monkeypatch, full_web_config):
        """get_config_path() returns the path used for loading."""
        config_file = _write_config_yaml(tmp_path, full_web_config)
        monkeypatch.setenv("M365_ADMIN_CONFIG", str(config_file))
        get_config()
        assert get_config_path() == str(config_file)

    def test_config_path_none_before_load(self):
        """get_config_path() is None before any config is loaded."""
        assert get_config_path() is None

    def test_raises_when_web_section_missing(self, tmp_path, monkeypatch):
        """Config without a web: section raises RuntimeError."""
        config_data = {
            "auth": {
                "client_id": "test-id",
                "tenant_id": "test-tenant",
                "scopes": ["User.Read"],
                "token_cache_path": str(tmp_path / "cache.json"),
            },
            "service": {
                "mode": "cli",
                "log_level": "INFO",
                "json_logs": False,
                "continuous_poll_seconds": 30,
                "max_consecutive_auth_failures": 5,
            },
            "storage": {
                "backend": "local",
                "local": {"base_path": str(tmp_path / "vault")},
            },
            "graph": {
                "max_retries": 3,
                "backoff_base_ms": 2000,
                "timeout_seconds": 30,
                "max_pages": 100,
            },
            "state": {"state_file_path": str(tmp_path / "state.json")},
            "extractors": {
                "email": {
                    "enabled": False,
                    "poll_interval_minutes": 3,
                    "folders": ["Inbox"],
                    "lookback_days": 30,
                    "max_items_per_sync": 100,
                },
                "calendar": {
                    "enabled": False,
                    "poll_interval_minutes": 60,
                    "lookback_days": 30,
                    "forward_days": 90,
                },
                "teams_chats": {
                    "enabled": False,
                    "poll_interval_minutes": 5,
                    "max_messages_per_chat": 200,
                },
                "teams_channels": {
                    "enabled": False,
                    "poll_interval_minutes": 5,
                },
                "onedrive": {
                    "enabled": False,
                    "poll_interval_minutes": 120,
                    "eager_convert_patterns": [],
                    "convertible_extensions": [".docx"],
                    "max_file_size_mb": 100,
                },
                "sharepoint": {
                    "enabled": False,
                    "poll_interval_minutes": 240,
                    "eager_convert_patterns": [],
                    "convertible_extensions": [".docx"],
                    "max_file_size_mb": 100,
                },
                "contacts": {
                    "enabled": False,
                    "poll_interval_minutes": 1440,
                    "max_items_per_sync": 500,
                    "include_contact_folders": False,
                },
                "directory": {
                    "enabled": False,
                    "poll_interval_minutes": 10080,
                    "include_manager_chain": True,
                    "include_direct_reports": True,
                    "only_active_users": True,
                },
            },
            "converters": {
                "backends": {"default": "native"},
                "extraction": {
                    "timeout_seconds": 30,
                    "max_file_size_mb": 100,
                    "xlsx_max_rows_per_sheet": 500,
                },
            },
            # No 'web' section — should raise
        }
        config_file = tmp_path / "config_no_web.yaml"
        config_file.write_text(yaml.dump(config_data))
        monkeypatch.setenv("M365_ADMIN_CONFIG", str(config_file))

        with pytest.raises(RuntimeError, match="config.web is None"):
            get_config()
