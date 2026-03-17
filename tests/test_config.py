"""Tests for config loading and validation."""

from __future__ import annotations

import os
import textwrap

import pytest

from m365_extract.config import Config, load_config


class TestLoadConfig:
    """Tests for load_config."""

    def test_loads_valid_config(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            textwrap.dedent("""\
            auth:
              client_id: "test-id"
              tenant_id: "test-tenant"
              scopes: ["User.Read", "Mail.Read"]
              token_cache_path: "./state/token_cache.json"
            service:
              mode: "cli"
              log_level: "INFO"
            storage:
              backend: "local"
              local:
                base_path: "./vault"
            graph:
              max_retries: 3
              backoff_base_ms: 2000
              timeout_seconds: 30
              max_pages: 100
            state:
              state_file_path: "./state/sync_state.json"
            extractors:
              email:
                enabled: true
                poll_interval_minutes: 3
                folders: ["Inbox"]
                lookback_days: 365
                max_items_per_sync: 500
              calendar:
                enabled: true
                poll_interval_minutes: 60
                lookback_days: 365
              teams_chats:
                enabled: true
                poll_interval_minutes: 5
                max_messages_per_chat: 200
              teams_channels:
                enabled: false
                poll_interval_minutes: 5
              onedrive:
                enabled: false
                poll_interval_minutes: 120
                eager_convert_patterns: []
                convertible_extensions: [".docx", ".pdf"]
                max_file_size_mb: 100
              sharepoint:
                enabled: false
                poll_interval_minutes: 240
                eager_convert_patterns: []
                convertible_extensions: [".docx", ".pdf"]
                max_file_size_mb: 100
              contacts:
                enabled: false
                poll_interval_minutes: 1440
              directory:
                enabled: false
                poll_interval_minutes: 10080
            converters:
              backends:
                pdf: "markitdown"
                docx: "markitdown"
                default: "native"
              extraction:
                timeout_seconds: 30
                max_file_size_mb: 100
                xlsx_max_rows_per_sheet: 500
        """)
        )
        config = load_config(str(config_file))
        assert isinstance(config, Config)
        assert config.auth.client_id == "test-id"
        assert config.graph.max_retries == 3
        assert config.extractors.email.enabled is True
        assert config.extractors.email.folders == ["Inbox"]
        assert isinstance(config.converters, dict)
        assert config.converters["backends"]["pdf"] == "markitdown"

    def test_missing_key_crashes(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            textwrap.dedent("""\
            auth:
              client_id: "test-id"
        """)
        )
        with pytest.raises(SystemExit):
            load_config(str(config_file))

    def test_wrong_type_crashes(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            textwrap.dedent("""\
            auth:
              client_id: "test-id"
              tenant_id: "test-tenant"
              scopes: "not-a-list"
              token_cache_path: "./state/token_cache.json"
            service:
              mode: "cli"
              log_level: "INFO"
            storage:
              backend: "local"
              local:
                base_path: "./vault"
            graph:
              max_retries: 3
              backoff_base_ms: 2000
              timeout_seconds: 30
              max_pages: 100
            state:
              state_file_path: "./state/sync_state.json"
            extractors:
              email:
                enabled: true
                poll_interval_minutes: 3
                folders: ["Inbox"]
                lookback_days: 365
                max_items_per_sync: 500
              calendar:
                enabled: true
                poll_interval_minutes: 60
                lookback_days: 365
              teams_chats:
                enabled: true
                poll_interval_minutes: 5
                max_messages_per_chat: 200
              teams_channels:
                enabled: false
                poll_interval_minutes: 5
              onedrive:
                enabled: false
                poll_interval_minutes: 120
                eager_convert_patterns: []
                convertible_extensions: [".docx", ".pdf"]
                max_file_size_mb: 100
              sharepoint:
                enabled: false
                poll_interval_minutes: 240
                eager_convert_patterns: []
                convertible_extensions: [".docx", ".pdf"]
                max_file_size_mb: 100
              contacts:
                enabled: false
                poll_interval_minutes: 1440
              directory:
                enabled: false
                poll_interval_minutes: 10080
            converters:
              backends:
                pdf: "markitdown"
                docx: "markitdown"
                default: "native"
              extraction:
                timeout_seconds: 30
                max_file_size_mb: 100
                xlsx_max_rows_per_sheet: 500
        """)
        )
        with pytest.raises(SystemExit):
            load_config(str(config_file))

    def test_missing_file_crashes(self, tmp_path):
        with pytest.raises(SystemExit):
            load_config(str(tmp_path / "nonexistent.yaml"))

    def test_env_var_expansion(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TEST_CLIENT_ID", "expanded-id")
        monkeypatch.setenv("TEST_TENANT_ID", "expanded-tenant")
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            textwrap.dedent("""\
            auth:
              client_id: "${TEST_CLIENT_ID}"
              tenant_id: "${TEST_TENANT_ID}"
              scopes: ["User.Read"]
              token_cache_path: "./state/token_cache.json"
            service:
              mode: "cli"
              log_level: "INFO"
            storage:
              backend: "local"
              local:
                base_path: "./vault"
            graph:
              max_retries: 3
              backoff_base_ms: 2000
              timeout_seconds: 30
              max_pages: 100
            state:
              state_file_path: "./state/sync_state.json"
            extractors:
              email:
                enabled: true
                poll_interval_minutes: 3
                folders: ["Inbox"]
                lookback_days: 365
                max_items_per_sync: 500
              calendar:
                enabled: true
                poll_interval_minutes: 60
                lookback_days: 365
              teams_chats:
                enabled: true
                poll_interval_minutes: 5
                max_messages_per_chat: 200
              teams_channels:
                enabled: false
                poll_interval_minutes: 5
              onedrive:
                enabled: false
                poll_interval_minutes: 120
                eager_convert_patterns: []
                convertible_extensions: [".docx", ".pdf"]
                max_file_size_mb: 100
              sharepoint:
                enabled: false
                poll_interval_minutes: 240
                eager_convert_patterns: []
                convertible_extensions: [".docx", ".pdf"]
                max_file_size_mb: 100
              contacts:
                enabled: false
                poll_interval_minutes: 1440
              directory:
                enabled: false
                poll_interval_minutes: 10080
            converters:
              backends:
                pdf: "markitdown"
                docx: "markitdown"
                default: "native"
              extraction:
                timeout_seconds: 30
                max_file_size_mb: 100
                xlsx_max_rows_per_sheet: 500
        """)
        )
        config = load_config(str(config_file))
        assert config.auth.client_id == "expanded-id"
        assert config.auth.tenant_id == "expanded-tenant"

    def test_missing_env_var_crashes(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            textwrap.dedent("""\
            auth:
              client_id: "${NONEXISTENT_VAR_12345}"
              tenant_id: "test"
              scopes: ["User.Read"]
              token_cache_path: "./state/token_cache.json"
            service:
              mode: "cli"
              log_level: "INFO"
            storage:
              backend: "local"
              local:
                base_path: "./vault"
            graph:
              max_retries: 3
              backoff_base_ms: 2000
              timeout_seconds: 30
              max_pages: 100
            state:
              state_file_path: "./state/sync_state.json"
            extractors:
              email:
                enabled: true
                poll_interval_minutes: 3
                folders: ["Inbox"]
                lookback_days: 365
                max_items_per_sync: 500
              calendar:
                enabled: true
                poll_interval_minutes: 60
                lookback_days: 365
              teams_chats:
                enabled: true
                poll_interval_minutes: 5
                max_messages_per_chat: 200
              teams_channels:
                enabled: false
                poll_interval_minutes: 5
              onedrive:
                enabled: false
                poll_interval_minutes: 120
                eager_convert_patterns: []
                convertible_extensions: [".docx", ".pdf"]
                max_file_size_mb: 100
              sharepoint:
                enabled: false
                poll_interval_minutes: 240
                eager_convert_patterns: []
                convertible_extensions: [".docx", ".pdf"]
                max_file_size_mb: 100
              contacts:
                enabled: false
                poll_interval_minutes: 1440
              directory:
                enabled: false
                poll_interval_minutes: 10080
            converters:
              backends:
                pdf: "markitdown"
                docx: "markitdown"
                default: "native"
              extraction:
                timeout_seconds: 30
                max_file_size_mb: 100
                xlsx_max_rows_per_sheet: 500
        """)
        )
        # Ensure the variable is not set
        os.environ.pop("NONEXISTENT_VAR_12345", None)
        with pytest.raises(SystemExit):
            load_config(str(config_file))

    def test_paths_resolved_relative_to_config_dir(self, tmp_path):
        """Relative paths in config should resolve against the config file's directory, not CWD."""
        subdir = tmp_path / "project" / "sub"
        subdir.mkdir(parents=True)
        config_file = subdir / "config.yaml"
        config_file.write_text(
            textwrap.dedent("""\
            auth:
              client_id: "test-id"
              tenant_id: "test-tenant"
              scopes: ["User.Read"]
              token_cache_path: "./state/token_cache.json"
            service:
              mode: "cli"
              log_level: "INFO"
            storage:
              backend: "local"
              local:
                base_path: "./vault"
            graph:
              max_retries: 3
              backoff_base_ms: 2000
              timeout_seconds: 30
              max_pages: 100
            state:
              state_file_path: "./state/sync_state.json"
            extractors:
              email:
                enabled: true
                poll_interval_minutes: 3
                folders: ["Inbox"]
                lookback_days: 365
                max_items_per_sync: 500
              calendar:
                enabled: true
                poll_interval_minutes: 60
                lookback_days: 365
              teams_chats:
                enabled: true
                poll_interval_minutes: 5
                max_messages_per_chat: 200
              teams_channels:
                enabled: false
                poll_interval_minutes: 5
              onedrive:
                enabled: false
                poll_interval_minutes: 120
                eager_convert_patterns: []
                convertible_extensions: [".docx", ".pdf"]
                max_file_size_mb: 100
              sharepoint:
                enabled: false
                poll_interval_minutes: 240
                eager_convert_patterns: []
                convertible_extensions: [".docx", ".pdf"]
                max_file_size_mb: 100
              contacts:
                enabled: false
                poll_interval_minutes: 1440
              directory:
                enabled: false
                poll_interval_minutes: 10080
            converters:
              backends:
                pdf: "markitdown"
                docx: "markitdown"
                default: "native"
              extraction:
                timeout_seconds: 30
                max_file_size_mb: 100
                xlsx_max_rows_per_sheet: 500
        """)
        )
        config = load_config(str(config_file))
        # Paths must be resolved relative to the config file's directory (subdir), not CWD
        assert str(subdir.resolve()) in config.storage.local.base_path
        assert str(subdir.resolve()) in config.state.state_file_path
        assert str(subdir.resolve()) in config.auth.token_cache_path

    def test_bool_not_accepted_as_int(self, tmp_path):
        """YAML 'true' should not be accepted where int is expected."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            textwrap.dedent("""\
            auth:
              client_id: "test-id"
              tenant_id: "test-tenant"
              scopes: ["User.Read"]
              token_cache_path: "./state/token_cache.json"
            service:
              mode: "cli"
              log_level: "INFO"
            storage:
              backend: "local"
              local:
                base_path: "./vault"
            graph:
              max_retries: true
              backoff_base_ms: 2000
              timeout_seconds: 30
              max_pages: 100
            state:
              state_file_path: "./state/sync_state.json"
            extractors:
              email:
                enabled: true
                poll_interval_minutes: 3
                folders: ["Inbox"]
                lookback_days: 365
                max_items_per_sync: 500
              calendar:
                enabled: true
                poll_interval_minutes: 60
                lookback_days: 365
              teams_chats:
                enabled: true
                poll_interval_minutes: 5
                max_messages_per_chat: 200
              teams_channels:
                enabled: false
                poll_interval_minutes: 5
              onedrive:
                enabled: false
                poll_interval_minutes: 120
                eager_convert_patterns: []
                convertible_extensions: [".docx", ".pdf"]
                max_file_size_mb: 100
              sharepoint:
                enabled: false
                poll_interval_minutes: 240
                eager_convert_patterns: []
                convertible_extensions: [".docx", ".pdf"]
                max_file_size_mb: 100
              contacts:
                enabled: false
                poll_interval_minutes: 1440
              directory:
                enabled: false
                poll_interval_minutes: 10080
            converters:
              backends:
                pdf: "markitdown"
                docx: "markitdown"
                default: "native"
              extraction:
                timeout_seconds: 30
                max_file_size_mb: 100
                xlsx_max_rows_per_sheet: 500
        """)
        )
        with pytest.raises(SystemExit):
            load_config(str(config_file))

    def test_azure_blob_config_loads(self, tmp_path):
        """Config with azure_blob backend and no local section loads correctly."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            textwrap.dedent("""\
            auth:
              client_id: "test-id"
              tenant_id: "test-tenant"
              scopes: ["User.Read"]
              token_cache_path: "./state/token_cache.json"
            service:
              mode: "cli"
              log_level: "INFO"
            storage:
              backend: "azure_blob"
              azure_blob:
                connection_string: "DefaultEndpointsProtocol=http;AccountName=dev;"
                container_name: "test-container"
                prefix: "user1/"
            graph:
              max_retries: 3
              backoff_base_ms: 2000
              timeout_seconds: 30
              max_pages: 100
            state:
              state_file_path: "./state/sync_state.json"
            extractors:
              email:
                enabled: true
                poll_interval_minutes: 3
                folders: ["Inbox"]
                lookback_days: 365
                max_items_per_sync: 500
              calendar:
                enabled: true
                poll_interval_minutes: 60
                lookback_days: 365
              teams_chats:
                enabled: true
                poll_interval_minutes: 5
                max_messages_per_chat: 200
              teams_channels:
                enabled: false
                poll_interval_minutes: 5
              onedrive:
                enabled: false
                poll_interval_minutes: 120
                eager_convert_patterns: []
                convertible_extensions: [".docx", ".pdf"]
                max_file_size_mb: 100
              sharepoint:
                enabled: false
                poll_interval_minutes: 240
                eager_convert_patterns: []
                convertible_extensions: [".docx", ".pdf"]
                max_file_size_mb: 100
              contacts:
                enabled: false
                poll_interval_minutes: 1440
              directory:
                enabled: false
                poll_interval_minutes: 10080
            converters:
              backends:
                pdf: "markitdown"
                docx: "markitdown"
                default: "native"
              extraction:
                timeout_seconds: 30
                max_file_size_mb: 100
                xlsx_max_rows_per_sheet: 500
        """)
        )
        config = load_config(str(config_file))
        assert config.storage.backend == "azure_blob"
        assert config.storage.local is None
        assert config.storage.azure_blob.connection_string == "DefaultEndpointsProtocol=http;AccountName=dev;"
        assert config.storage.azure_blob.container_name == "test-container"
        assert config.storage.azure_blob.prefix == "user1/"

    def test_local_config_without_azure_blob_section(self, tmp_path):
        """Config with local backend and no azure_blob section loads correctly."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            textwrap.dedent("""\
            auth:
              client_id: "test-id"
              tenant_id: "test-tenant"
              scopes: ["User.Read"]
              token_cache_path: "./state/token_cache.json"
            service:
              mode: "cli"
              log_level: "INFO"
            storage:
              backend: "local"
              local:
                base_path: "./vault"
            graph:
              max_retries: 3
              backoff_base_ms: 2000
              timeout_seconds: 30
              max_pages: 100
            state:
              state_file_path: "./state/sync_state.json"
            extractors:
              email:
                enabled: true
                poll_interval_minutes: 3
                folders: ["Inbox"]
                lookback_days: 365
                max_items_per_sync: 500
              calendar:
                enabled: true
                poll_interval_minutes: 60
                lookback_days: 365
              teams_chats:
                enabled: true
                poll_interval_minutes: 5
                max_messages_per_chat: 200
              teams_channels:
                enabled: false
                poll_interval_minutes: 5
              onedrive:
                enabled: false
                poll_interval_minutes: 120
                eager_convert_patterns: []
                convertible_extensions: [".docx", ".pdf"]
                max_file_size_mb: 100
              sharepoint:
                enabled: false
                poll_interval_minutes: 240
                eager_convert_patterns: []
                convertible_extensions: [".docx", ".pdf"]
                max_file_size_mb: 100
              contacts:
                enabled: false
                poll_interval_minutes: 1440
              directory:
                enabled: false
                poll_interval_minutes: 10080
            converters:
              backends:
                pdf: "markitdown"
                docx: "markitdown"
                default: "native"
              extraction:
                timeout_seconds: 30
                max_file_size_mb: 100
                xlsx_max_rows_per_sheet: 500
        """)
        )
        config = load_config(str(config_file))
        assert config.storage.backend == "local"
        assert config.storage.azure_blob is None
        assert config.storage.local is not None

    def test_azure_blob_missing_required_key_crashes(self, tmp_path):
        """Missing key inside azure_blob section should crash."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            textwrap.dedent("""\
            auth:
              client_id: "test-id"
              tenant_id: "test-tenant"
              scopes: ["User.Read"]
              token_cache_path: "./state/token_cache.json"
            service:
              mode: "cli"
              log_level: "INFO"
            storage:
              backend: "azure_blob"
              azure_blob:
                connection_string: "some-conn"
            graph:
              max_retries: 3
              backoff_base_ms: 2000
              timeout_seconds: 30
              max_pages: 100
            state:
              state_file_path: "./state/sync_state.json"
            extractors:
              email:
                enabled: true
                poll_interval_minutes: 3
                folders: ["Inbox"]
                lookback_days: 365
                max_items_per_sync: 500
              calendar:
                enabled: true
                poll_interval_minutes: 60
                lookback_days: 365
              teams_chats:
                enabled: true
                poll_interval_minutes: 5
                max_messages_per_chat: 200
              teams_channels:
                enabled: false
                poll_interval_minutes: 5
              onedrive:
                enabled: false
                poll_interval_minutes: 120
                eager_convert_patterns: []
                convertible_extensions: [".docx", ".pdf"]
                max_file_size_mb: 100
              sharepoint:
                enabled: false
                poll_interval_minutes: 240
                eager_convert_patterns: []
                convertible_extensions: [".docx", ".pdf"]
                max_file_size_mb: 100
              contacts:
                enabled: false
                poll_interval_minutes: 1440
              directory:
                enabled: false
                poll_interval_minutes: 10080
            converters:
              backends:
                pdf: "markitdown"
                docx: "markitdown"
                default: "native"
              extraction:
                timeout_seconds: 30
                max_file_size_mb: 100
                xlsx_max_rows_per_sheet: 500
        """)
        )
        with pytest.raises(SystemExit):
            load_config(str(config_file))
