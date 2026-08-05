"""Tests for config loading and validation."""

from __future__ import annotations

import os
import textwrap

import pytest
from pydantic import ValidationError

from m365_brain.config import (
    Config,
    ConfigError,
    ConvertersConfig,
    ExplicitChannel,
    TeamsChannelsExtractorConfig,
    load_config,
)
from m365_brain.config.loader import _deep_merge


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
              json_logs: false
              continuous_poll_seconds: 30
              max_consecutive_auth_failures: 5
            storage:
              backend: "local"
              local:
                base_path: "./vault"
            graph:
              max_retries: 3
              backoff_base_ms: 2000
              timeout_seconds: 30
              max_pages: 100
              max_retry_after_seconds: 300.0
              error_message_max_length: 200
            extractors:
              email:
                enabled: true
                poll_interval_minutes: 3
                mailboxes:
                  - address: "me"
                    folders: ["Inbox"]
                    output_subdir: ""
                lookback_days: 365
                max_items_per_sync: 500
                download_attachments: false
                max_attachment_size_mb: 25
                attachment_convert_extensions: []
              calendar:
                enabled: true
                poll_interval_minutes: 60
                lookback_days: 365
                forward_days: 90
              teams_chats:
                enabled: true
                poll_interval_minutes: 5
                max_messages_per_chat: 200
                download_attachments: false
                download_inline_images: false
                max_attachment_size_mb: 25
                attachment_convert_extensions: []
              teams_channels:
                enabled: false
                poll_interval_minutes: 5
                max_messages_per_channel: 200
                channels: null
                download_attachments: false
                download_inline_images: false
                max_attachment_size_mb: 25
                attachment_convert_extensions: []
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
                max_items_per_sync: 500
                include_contact_folders: false
              directory:
                enabled: false
                poll_interval_minutes: 10080
                include_manager_chain: true
                include_direct_reports: true
                only_active_users: true
            converters:
              backends:
                pdf: "markitdown"
                docx: "markitdown"
                default: "native"
              extraction:
                timeout_seconds: 30
                max_file_size_mb: 100
                xlsx_max_rows_per_sheet: 500
                isolation: "thread"
              slug_max_length: 80
              hash_length: 6
        """)
        )
        config = load_config(str(config_file))
        assert isinstance(config, Config)
        assert config.auth.client_id == "test-id"
        assert config.graph.max_retries == 3
        assert config.extractors.email.enabled is True
        assert len(config.extractors.email.mailboxes) == 1
        assert config.extractors.email.mailboxes[0].address == "me"
        assert config.extractors.email.mailboxes[0].folders == ["Inbox"]
        assert config.extractors.email.mailboxes[0].output_subdir == ""
        assert isinstance(config.converters, ConvertersConfig)
        assert config.converters.backends["pdf"] == "markitdown"

    def test_missing_key_crashes(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            textwrap.dedent("""\
            auth:
              client_id: "test-id"
        """)
        )
        with pytest.raises(ConfigError):
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
              json_logs: false
              continuous_poll_seconds: 30
              max_consecutive_auth_failures: 5
            storage:
              backend: "local"
              local:
                base_path: "./vault"
            graph:
              max_retries: 3
              backoff_base_ms: 2000
              timeout_seconds: 30
              max_pages: 100
              max_retry_after_seconds: 300.0
              error_message_max_length: 200
            extractors:
              email:
                enabled: true
                poll_interval_minutes: 3
                mailboxes:
                  - address: "me"
                    folders: ["Inbox"]
                    output_subdir: ""
                lookback_days: 365
                max_items_per_sync: 500
                download_attachments: false
                max_attachment_size_mb: 25
                attachment_convert_extensions: []
              calendar:
                enabled: true
                poll_interval_minutes: 60
                lookback_days: 365
                forward_days: 90
              teams_chats:
                enabled: true
                poll_interval_minutes: 5
                max_messages_per_chat: 200
                download_attachments: false
                download_inline_images: false
                max_attachment_size_mb: 25
                attachment_convert_extensions: []
              teams_channels:
                enabled: false
                poll_interval_minutes: 5
                max_messages_per_channel: 200
                channels: null
                download_attachments: false
                download_inline_images: false
                max_attachment_size_mb: 25
                attachment_convert_extensions: []
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
                max_items_per_sync: 500
                include_contact_folders: false
              directory:
                enabled: false
                poll_interval_minutes: 10080
                include_manager_chain: true
                include_direct_reports: true
                only_active_users: true
            converters:
              backends:
                pdf: "markitdown"
                docx: "markitdown"
                default: "native"
              extraction:
                timeout_seconds: 30
                max_file_size_mb: 100
                xlsx_max_rows_per_sheet: 500
                isolation: "thread"
              slug_max_length: 80
              hash_length: 6
        """)
        )
        with pytest.raises(ConfigError):
            load_config(str(config_file))

    def test_missing_file_crashes(self, tmp_path):
        with pytest.raises(ConfigError):
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
              json_logs: false
              continuous_poll_seconds: 30
              max_consecutive_auth_failures: 5
            storage:
              backend: "local"
              local:
                base_path: "./vault"
            graph:
              max_retries: 3
              backoff_base_ms: 2000
              timeout_seconds: 30
              max_pages: 100
              max_retry_after_seconds: 300.0
              error_message_max_length: 200
            extractors:
              email:
                enabled: true
                poll_interval_minutes: 3
                mailboxes:
                  - address: "me"
                    folders: ["Inbox"]
                    output_subdir: ""
                lookback_days: 365
                max_items_per_sync: 500
                download_attachments: false
                max_attachment_size_mb: 25
                attachment_convert_extensions: []
              calendar:
                enabled: true
                poll_interval_minutes: 60
                lookback_days: 365
                forward_days: 90
              teams_chats:
                enabled: true
                poll_interval_minutes: 5
                max_messages_per_chat: 200
                download_attachments: false
                download_inline_images: false
                max_attachment_size_mb: 25
                attachment_convert_extensions: []
              teams_channels:
                enabled: false
                poll_interval_minutes: 5
                max_messages_per_channel: 200
                channels: null
                download_attachments: false
                download_inline_images: false
                max_attachment_size_mb: 25
                attachment_convert_extensions: []
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
                max_items_per_sync: 500
                include_contact_folders: false
              directory:
                enabled: false
                poll_interval_minutes: 10080
                include_manager_chain: true
                include_direct_reports: true
                only_active_users: true
            converters:
              backends:
                pdf: "markitdown"
                docx: "markitdown"
                default: "native"
              extraction:
                timeout_seconds: 30
                max_file_size_mb: 100
                xlsx_max_rows_per_sheet: 500
                isolation: "thread"
              slug_max_length: 80
              hash_length: 6
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
              json_logs: false
              continuous_poll_seconds: 30
              max_consecutive_auth_failures: 5
            storage:
              backend: "local"
              local:
                base_path: "./vault"
            graph:
              max_retries: 3
              backoff_base_ms: 2000
              timeout_seconds: 30
              max_pages: 100
              max_retry_after_seconds: 300.0
              error_message_max_length: 200
            extractors:
              email:
                enabled: true
                poll_interval_minutes: 3
                mailboxes:
                  - address: "me"
                    folders: ["Inbox"]
                    output_subdir: ""
                lookback_days: 365
                max_items_per_sync: 500
                download_attachments: false
                max_attachment_size_mb: 25
                attachment_convert_extensions: []
              calendar:
                enabled: true
                poll_interval_minutes: 60
                lookback_days: 365
                forward_days: 90
              teams_chats:
                enabled: true
                poll_interval_minutes: 5
                max_messages_per_chat: 200
                download_attachments: false
                download_inline_images: false
                max_attachment_size_mb: 25
                attachment_convert_extensions: []
              teams_channels:
                enabled: false
                poll_interval_minutes: 5
                max_messages_per_channel: 200
                channels: null
                download_attachments: false
                download_inline_images: false
                max_attachment_size_mb: 25
                attachment_convert_extensions: []
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
                max_items_per_sync: 500
                include_contact_folders: false
              directory:
                enabled: false
                poll_interval_minutes: 10080
                include_manager_chain: true
                include_direct_reports: true
                only_active_users: true
            converters:
              backends:
                pdf: "markitdown"
                docx: "markitdown"
                default: "native"
              extraction:
                timeout_seconds: 30
                max_file_size_mb: 100
                xlsx_max_rows_per_sheet: 500
                isolation: "thread"
              slug_max_length: 80
              hash_length: 6
        """)
        )
        # Ensure the variable is not set
        os.environ.pop("NONEXISTENT_VAR_12345", None)
        with pytest.raises(ConfigError):
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
              json_logs: false
              continuous_poll_seconds: 30
              max_consecutive_auth_failures: 5
            storage:
              backend: "local"
              local:
                base_path: "./vault"
            graph:
              max_retries: 3
              backoff_base_ms: 2000
              timeout_seconds: 30
              max_pages: 100
              max_retry_after_seconds: 300.0
              error_message_max_length: 200
            extractors:
              email:
                enabled: true
                poll_interval_minutes: 3
                mailboxes:
                  - address: "me"
                    folders: ["Inbox"]
                    output_subdir: ""
                lookback_days: 365
                max_items_per_sync: 500
                download_attachments: false
                max_attachment_size_mb: 25
                attachment_convert_extensions: []
              calendar:
                enabled: true
                poll_interval_minutes: 60
                lookback_days: 365
                forward_days: 90
              teams_chats:
                enabled: true
                poll_interval_minutes: 5
                max_messages_per_chat: 200
                download_attachments: false
                download_inline_images: false
                max_attachment_size_mb: 25
                attachment_convert_extensions: []
              teams_channels:
                enabled: false
                poll_interval_minutes: 5
                max_messages_per_channel: 200
                channels: null
                download_attachments: false
                download_inline_images: false
                max_attachment_size_mb: 25
                attachment_convert_extensions: []
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
                max_items_per_sync: 500
                include_contact_folders: false
              directory:
                enabled: false
                poll_interval_minutes: 10080
                include_manager_chain: true
                include_direct_reports: true
                only_active_users: true
            converters:
              backends:
                pdf: "markitdown"
                docx: "markitdown"
                default: "native"
              extraction:
                timeout_seconds: 30
                max_file_size_mb: 100
                xlsx_max_rows_per_sheet: 500
                isolation: "thread"
              slug_max_length: 80
              hash_length: 6
        """)
        )
        config = load_config(str(config_file))
        # Paths must be resolved relative to the config file's directory (subdir), not CWD
        assert str(subdir.resolve()) in config.storage.local.base_path
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
              json_logs: false
              continuous_poll_seconds: 30
              max_consecutive_auth_failures: 5
            storage:
              backend: "local"
              local:
                base_path: "./vault"
            graph:
              max_retries: true
              backoff_base_ms: 2000
              timeout_seconds: 30
              max_pages: 100
              max_retry_after_seconds: 300.0
              error_message_max_length: 200
            extractors:
              email:
                enabled: true
                poll_interval_minutes: 3
                mailboxes:
                  - address: "me"
                    folders: ["Inbox"]
                    output_subdir: ""
                lookback_days: 365
                max_items_per_sync: 500
                download_attachments: false
                max_attachment_size_mb: 25
                attachment_convert_extensions: []
              calendar:
                enabled: true
                poll_interval_minutes: 60
                lookback_days: 365
                forward_days: 90
              teams_chats:
                enabled: true
                poll_interval_minutes: 5
                max_messages_per_chat: 200
                download_attachments: false
                download_inline_images: false
                max_attachment_size_mb: 25
                attachment_convert_extensions: []
              teams_channels:
                enabled: false
                poll_interval_minutes: 5
                max_messages_per_channel: 200
                channels: null
                download_attachments: false
                download_inline_images: false
                max_attachment_size_mb: 25
                attachment_convert_extensions: []
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
                max_items_per_sync: 500
                include_contact_folders: false
              directory:
                enabled: false
                poll_interval_minutes: 10080
                include_manager_chain: true
                include_direct_reports: true
                only_active_users: true
            converters:
              backends:
                pdf: "markitdown"
                docx: "markitdown"
                default: "native"
              extraction:
                timeout_seconds: 30
                max_file_size_mb: 100
                xlsx_max_rows_per_sheet: 500
                isolation: "thread"
              slug_max_length: 80
              hash_length: 6
        """)
        )
        with pytest.raises(ConfigError):
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
              json_logs: false
              continuous_poll_seconds: 30
              max_consecutive_auth_failures: 5
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
              max_retry_after_seconds: 300.0
              error_message_max_length: 200
            extractors:
              email:
                enabled: true
                poll_interval_minutes: 3
                mailboxes:
                  - address: "me"
                    folders: ["Inbox"]
                    output_subdir: ""
                lookback_days: 365
                max_items_per_sync: 500
                download_attachments: false
                max_attachment_size_mb: 25
                attachment_convert_extensions: []
              calendar:
                enabled: true
                poll_interval_minutes: 60
                lookback_days: 365
                forward_days: 90
              teams_chats:
                enabled: true
                poll_interval_minutes: 5
                max_messages_per_chat: 200
                download_attachments: false
                download_inline_images: false
                max_attachment_size_mb: 25
                attachment_convert_extensions: []
              teams_channels:
                enabled: false
                poll_interval_minutes: 5
                max_messages_per_channel: 200
                channels: null
                download_attachments: false
                download_inline_images: false
                max_attachment_size_mb: 25
                attachment_convert_extensions: []
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
                max_items_per_sync: 500
                include_contact_folders: false
              directory:
                enabled: false
                poll_interval_minutes: 10080
                include_manager_chain: true
                include_direct_reports: true
                only_active_users: true
            converters:
              backends:
                pdf: "markitdown"
                docx: "markitdown"
                default: "native"
              extraction:
                timeout_seconds: 30
                max_file_size_mb: 100
                xlsx_max_rows_per_sheet: 500
                isolation: "thread"
              slug_max_length: 80
              hash_length: 6
        """)
        )
        config = load_config(str(config_file))
        assert config.storage.backend == "azure_blob"
        assert config.storage.local is None
        assert (
            config.storage.azure_blob.connection_string.get_secret_value()
            == "DefaultEndpointsProtocol=http;AccountName=dev;"
        )
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
              json_logs: false
              continuous_poll_seconds: 30
              max_consecutive_auth_failures: 5
            storage:
              backend: "local"
              local:
                base_path: "./vault"
            graph:
              max_retries: 3
              backoff_base_ms: 2000
              timeout_seconds: 30
              max_pages: 100
              max_retry_after_seconds: 300.0
              error_message_max_length: 200
            extractors:
              email:
                enabled: true
                poll_interval_minutes: 3
                mailboxes:
                  - address: "me"
                    folders: ["Inbox"]
                    output_subdir: ""
                lookback_days: 365
                max_items_per_sync: 500
                download_attachments: false
                max_attachment_size_mb: 25
                attachment_convert_extensions: []
              calendar:
                enabled: true
                poll_interval_minutes: 60
                lookback_days: 365
                forward_days: 90
              teams_chats:
                enabled: true
                poll_interval_minutes: 5
                max_messages_per_chat: 200
                download_attachments: false
                download_inline_images: false
                max_attachment_size_mb: 25
                attachment_convert_extensions: []
              teams_channels:
                enabled: false
                poll_interval_minutes: 5
                max_messages_per_channel: 200
                channels: null
                download_attachments: false
                download_inline_images: false
                max_attachment_size_mb: 25
                attachment_convert_extensions: []
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
                max_items_per_sync: 500
                include_contact_folders: false
              directory:
                enabled: false
                poll_interval_minutes: 10080
                include_manager_chain: true
                include_direct_reports: true
                only_active_users: true
            converters:
              backends:
                pdf: "markitdown"
                docx: "markitdown"
                default: "native"
              extraction:
                timeout_seconds: 30
                max_file_size_mb: 100
                xlsx_max_rows_per_sheet: 500
                isolation: "thread"
              slug_max_length: 80
              hash_length: 6
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
              json_logs: false
              continuous_poll_seconds: 30
              max_consecutive_auth_failures: 5
            storage:
              backend: "azure_blob"
              azure_blob:
                connection_string: "some-conn"
            graph:
              max_retries: 3
              backoff_base_ms: 2000
              timeout_seconds: 30
              max_pages: 100
              max_retry_after_seconds: 300.0
              error_message_max_length: 200
            extractors:
              email:
                enabled: true
                poll_interval_minutes: 3
                mailboxes:
                  - address: "me"
                    folders: ["Inbox"]
                    output_subdir: ""
                lookback_days: 365
                max_items_per_sync: 500
                download_attachments: false
                max_attachment_size_mb: 25
                attachment_convert_extensions: []
              calendar:
                enabled: true
                poll_interval_minutes: 60
                lookback_days: 365
                forward_days: 90
              teams_chats:
                enabled: true
                poll_interval_minutes: 5
                max_messages_per_chat: 200
                download_attachments: false
                download_inline_images: false
                max_attachment_size_mb: 25
                attachment_convert_extensions: []
              teams_channels:
                enabled: false
                poll_interval_minutes: 5
                max_messages_per_channel: 200
                channels: null
                download_attachments: false
                download_inline_images: false
                max_attachment_size_mb: 25
                attachment_convert_extensions: []
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
                max_items_per_sync: 500
                include_contact_folders: false
              directory:
                enabled: false
                poll_interval_minutes: 10080
                include_manager_chain: true
                include_direct_reports: true
                only_active_users: true
            converters:
              backends:
                pdf: "markitdown"
                docx: "markitdown"
                default: "native"
              extraction:
                timeout_seconds: 30
                max_file_size_mb: 100
                xlsx_max_rows_per_sheet: 500
                isolation: "thread"
              slug_max_length: 80
              hash_length: 6
        """)
        )
        with pytest.raises(ConfigError):
            load_config(str(config_file))


# Helper: minimal valid config YAML text for multi-path tests.
_MINIMAL_CONFIG = textwrap.dedent("""\
    auth:
      client_id: "test-id"
      tenant_id: "test-tenant"
      scopes: ["User.Read", "Mail.Read"]
      token_cache_path: "./state/token_cache.json"
    service:
      mode: "cli"
      log_level: "INFO"
      json_logs: false
      continuous_poll_seconds: 30
      max_consecutive_auth_failures: 5
    storage:
      backend: "local"
      local:
        base_path: "./vault"
    graph:
      max_retries: 3
      backoff_base_ms: 2000
      timeout_seconds: 30
      max_pages: 100
      max_retry_after_seconds: 300.0
      error_message_max_length: 200
    extractors:
      email:
        enabled: true
        poll_interval_minutes: 3
        mailboxes:
          - address: "me"
            folders: ["Inbox"]
            output_subdir: ""
        lookback_days: 365
        max_items_per_sync: 500
        download_attachments: false
        max_attachment_size_mb: 25
        attachment_convert_extensions: []
      calendar:
        enabled: true
        poll_interval_minutes: 60
        lookback_days: 365
        forward_days: 90
      teams_chats:
        enabled: true
        poll_interval_minutes: 5
        max_messages_per_chat: 200
        download_attachments: false
        download_inline_images: false
        max_attachment_size_mb: 25
        attachment_convert_extensions: []
      teams_channels:
        enabled: false
        poll_interval_minutes: 5
        max_messages_per_channel: 200
        channels: null
        download_attachments: false
        download_inline_images: false
        max_attachment_size_mb: 25
        attachment_convert_extensions: []
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
        max_items_per_sync: 500
        include_contact_folders: false
      directory:
        enabled: false
        poll_interval_minutes: 10080
        include_manager_chain: true
        include_direct_reports: true
        only_active_users: true
    converters:
      backends:
        pdf: "markitdown"
        docx: "markitdown"
        default: "native"
      extraction:
        timeout_seconds: 30
        max_file_size_mb: 100
        xlsx_max_rows_per_sheet: 500
        isolation: "thread"
      slug_max_length: 80
      hash_length: 6
""")


class TestTeamsChannelsExplicitMode:
    """channels: null = discovery mode; populated list = explicit mode; empty list = misconfiguration."""

    @staticmethod
    def _base_kwargs() -> dict:
        return {
            "enabled": True,
            "poll_interval_minutes": 5,
            "max_messages_per_channel": 200,
            "download_attachments": False,
            "download_inline_images": False,
            "max_attachment_size_mb": 25,
            "attachment_convert_extensions": [],
        }

    def test_channels_null_is_discovery_mode(self):
        config = TeamsChannelsExtractorConfig(**self._base_kwargs(), channels=None)
        assert config.channels is None

    def test_channels_populated_list_is_explicit_mode(self):
        channel = ExplicitChannel(
            team_id="t-1",
            channel_id="19:abc@thread.tacv2",
            team_name="Engineering",
            channel_name="General",
        )
        config = TeamsChannelsExtractorConfig(**self._base_kwargs(), channels=[channel])
        assert config.channels == [channel]
        assert config.channels[0].channel_id == "19:abc@thread.tacv2"

    def test_channels_empty_list_raises(self):
        with pytest.raises(ValidationError, match="discovery mode"):
            TeamsChannelsExtractorConfig(**self._base_kwargs(), channels=[])

    def test_channels_field_is_required(self):
        with pytest.raises(ValidationError):
            TeamsChannelsExtractorConfig(**self._base_kwargs())

    @pytest.mark.parametrize("missing", ["team_id", "channel_id", "team_name", "channel_name"])
    def test_explicit_channel_missing_subfield_raises(self, missing: str):
        kwargs = {
            "team_id": "t-1",
            "channel_id": "19:abc@thread.tacv2",
            "team_name": "Engineering",
            "channel_name": "General",
        }
        del kwargs[missing]
        with pytest.raises(ValidationError):
            ExplicitChannel(**kwargs)

    def test_explicit_channels_load_from_yaml(self, tmp_path):
        yaml_text = _MINIMAL_CONFIG.replace(
            "    channels: null\n",
            "    channels:\n"
            '      - team_id: "t-1"\n'
            '        channel_id: "19:abc@thread.tacv2"\n'
            '        team_name: "Engineering"\n'
            '        channel_name: "General"\n',
        )
        assert yaml_text != _MINIMAL_CONFIG
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml_text)
        config = load_config(str(config_file))
        assert config.extractors.teams_channels.channels == [
            ExplicitChannel(
                team_id="t-1",
                channel_id="19:abc@thread.tacv2",
                team_name="Engineering",
                channel_name="General",
            )
        ]

    def test_empty_channels_list_in_yaml_raises(self, tmp_path):
        yaml_text = _MINIMAL_CONFIG.replace("    channels: null\n", "    channels: []\n")
        assert yaml_text != _MINIMAL_CONFIG
        config_file = tmp_path / "config.yaml"
        config_file.write_text(yaml_text)
        with pytest.raises(ConfigError, match="discovery mode"):
            load_config(str(config_file))


class TestDeepMerge:
    """Tests for the _deep_merge helper."""

    def test_empty_base(self):
        result = _deep_merge({}, {"a": 1})
        assert result == {"a": 1}

    def test_empty_override(self):
        result = _deep_merge({"a": 1}, {})
        assert result == {"a": 1}

    def test_both_empty(self):
        result = _deep_merge({}, {})
        assert result == {}

    def test_scalar_override(self):
        result = _deep_merge({"a": 1, "b": 2}, {"b": 99})
        assert result == {"a": 1, "b": 99}

    def test_list_override(self):
        """Lists from override replace base lists entirely (no concatenation)."""
        result = _deep_merge({"items": [1, 2, 3]}, {"items": [4, 5]})
        assert result == {"items": [4, 5]}

    def test_nested_dict_merge(self):
        base = {"a": {"x": 1, "y": 2}, "b": 3}
        override = {"a": {"y": 99, "z": 100}}
        result = _deep_merge(base, override)
        assert result == {"a": {"x": 1, "y": 99, "z": 100}, "b": 3}

    def test_deeply_nested(self):
        base = {"a": {"b": {"c": 1, "d": 2}}}
        override = {"a": {"b": {"d": 99}}}
        result = _deep_merge(base, override)
        assert result == {"a": {"b": {"c": 1, "d": 99}}}

    def test_override_dict_with_scalar(self):
        """Scalar in override replaces dict in base."""
        result = _deep_merge({"a": {"nested": True}}, {"a": "flat"})
        assert result == {"a": "flat"}

    def test_override_scalar_with_dict(self):
        """Dict in override replaces scalar in base."""
        result = _deep_merge({"a": "flat"}, {"a": {"nested": True}})
        assert result == {"a": {"nested": True}}

    def test_does_not_mutate_base(self):
        base = {"a": {"x": 1}}
        override = {"a": {"y": 2}}
        _deep_merge(base, override)
        assert base == {"a": {"x": 1}}

    def test_does_not_mutate_override(self):
        base = {"a": {"x": 1}}
        override = {"a": {"y": 2}}
        _deep_merge(base, override)
        assert override == {"a": {"y": 2}}


class TestMultiPathLoadConfig:
    """Tests for comma-separated multi-path load_config."""

    def test_single_path_still_works(self, tmp_path):
        """A single path (no comma) loads identically to the old behavior."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(_MINIMAL_CONFIG)
        config = load_config(str(config_file))
        assert isinstance(config, Config)
        assert config.auth.client_id == "test-id"

    def test_multi_path_merges(self, tmp_path):
        """Two files are deep-merged, with later files winning."""
        base_file = tmp_path / "base.yaml"
        base_file.write_text(_MINIMAL_CONFIG)

        override_file = tmp_path / "override.yaml"
        override_file.write_text(
            textwrap.dedent("""\
            auth:
              client_id: "overridden-id"
            service:
              log_level: "DEBUG"
        """)
        )

        config = load_config(f"{base_file},{override_file}")
        assert config.auth.client_id == "overridden-id"
        assert config.service.log_level == "DEBUG"
        # Values not overridden should remain from base
        assert config.auth.tenant_id == "test-tenant"
        assert config.graph.max_retries == 3

    def test_empty_yaml_file_skipped(self, tmp_path):
        """An empty (or comment-only) YAML file is skipped without error."""
        base_file = tmp_path / "base.yaml"
        base_file.write_text(_MINIMAL_CONFIG)

        empty_file = tmp_path / "empty.yaml"
        empty_file.write_text("# just a comment\n")

        config = load_config(f"{base_file},{empty_file}")
        assert isinstance(config, Config)
        assert config.auth.client_id == "test-id"

    def test_all_empty_files_raises(self, tmp_path):
        """If all files are empty, ConfigError is raised."""
        empty1 = tmp_path / "e1.yaml"
        empty1.write_text("# nothing\n")
        empty2 = tmp_path / "e2.yaml"
        empty2.write_text("")

        with pytest.raises(ConfigError, match="no config data found"):
            load_config(f"{empty1},{empty2}")

    def test_missing_file_in_multi_path_raises(self, tmp_path):
        """A missing file anywhere in the chain raises ConfigError."""
        base_file = tmp_path / "base.yaml"
        base_file.write_text(_MINIMAL_CONFIG)

        with pytest.raises(ConfigError, match="config file not found"):
            load_config(f"{base_file},{tmp_path / 'nonexistent.yaml'}")

    def test_non_mapping_file_in_chain_raises(self, tmp_path):
        """A YAML file containing a list (not mapping) raises ConfigError."""
        base_file = tmp_path / "base.yaml"
        base_file.write_text(_MINIMAL_CONFIG)

        bad_file = tmp_path / "bad.yaml"
        bad_file.write_text("- item1\n- item2\n")

        with pytest.raises(ConfigError, match="YAML mapping"):
            load_config(f"{base_file},{bad_file}")

    def test_paths_resolved_against_first_file(self, tmp_path):
        """Relative paths are resolved against the first config file's directory."""
        subdir = tmp_path / "project"
        subdir.mkdir()
        base_file = subdir / "base.yaml"
        base_file.write_text(_MINIMAL_CONFIG)

        override_dir = tmp_path / "overrides"
        override_dir.mkdir()
        override_file = override_dir / "service.yaml"
        override_file.write_text(
            textwrap.dedent("""\
            service:
              log_level: "WARNING"
        """)
        )

        config = load_config(f"{base_file},{override_file}")
        # storage.local.base_path ("./vault") should resolve against subdir (first file)
        assert str(subdir.resolve()) in config.storage.local.base_path

    def test_three_way_merge(self, tmp_path):
        """Three files merge left-to-right correctly."""
        base_file = tmp_path / "base.yaml"
        base_file.write_text(_MINIMAL_CONFIG)

        mid_file = tmp_path / "mid.yaml"
        mid_file.write_text(
            textwrap.dedent("""\
            auth:
              client_id: "mid-id"
            service:
              log_level: "DEBUG"
        """)
        )

        top_file = tmp_path / "top.yaml"
        top_file.write_text(
            textwrap.dedent("""\
            auth:
              client_id: "top-id"
        """)
        )

        config = load_config(f"{base_file},{mid_file},{top_file}")
        # top overrides mid
        assert config.auth.client_id == "top-id"
        # mid overrides base (not overridden by top)
        assert config.service.log_level == "DEBUG"
        # base values preserved
        assert config.graph.max_retries == 3

    def test_whitespace_in_path_list_is_stripped(self, tmp_path):
        """Spaces around commas in the path string are stripped."""
        config_file = tmp_path / "config.yaml"
        config_file.write_text(_MINIMAL_CONFIG)

        config = load_config(f"  {config_file} , {config_file}  ")
        assert isinstance(config, Config)

    def test_list_override_in_multi_path(self, tmp_path):
        """Lists from later files replace earlier lists entirely."""
        base_file = tmp_path / "base.yaml"
        base_file.write_text(_MINIMAL_CONFIG)

        override_file = tmp_path / "override.yaml"
        override_file.write_text(
            textwrap.dedent("""\
            extractors:
              email:
                mailboxes:
                  - address: "me"
                    folders: ["SentItems"]
                    output_subdir: ""
        """)
        )

        config = load_config(f"{base_file},{override_file}")
        assert config.extractors.email.mailboxes[0].folders == ["SentItems"]


class TestValidationErrorsDoNotEchoInput:
    """A config error must not print the value that failed.

    `str(ValidationError)` embeds `input_value`, and for a model-level error
    that input is the whole surrounding mapping. So one missing or misspelt key
    *next to* a secret used to print the secret.

    The leak is sneakier than it looks: pydantic truncates a long value to its
    **first two and last seven characters** -- `AccountKey=REAL...' renders as
    `'Ac...4567890'`. An assertion looking for the whole secret, or for its
    head, passes while the tail leaks. So these tests assert on a distinctive
    tail, and on the structural property that no input is echoed at all.

    `SecretStr` cannot close this: when validation fails the value is still a
    raw `str`, because the model it would have become never existed.
    """

    SECRET = "AccountKey=REALKEYMATERIAL/ZZDISTINCTIVETAILZZ"

    def _write(self, tmp_path, body: str):
        path = tmp_path / "c.yaml"
        path.write_text(body, encoding="utf-8")
        return path

    def _load_expecting_failure(self, tmp_path, body: str) -> str:
        with pytest.raises(ConfigError) as caught:
            load_config(str(self._write(tmp_path, body)))
        return str(caught.value)

    def test_a_secret_beside_a_missing_key_is_not_printed(self, tmp_path):
        message = self._load_expecting_failure(
            tmp_path,
            "storage:\n"
            "  backend: azure_blob\n"
            "  azure_blob:\n"
            f'    connection_string: "{self.SECRET}"\n'
            '    prefix: "p"\n',
        )
        assert self.SECRET not in message
        assert "REALKEYMATERIAL" not in message
        # The half that a naive assertion misses: pydantic keeps the last
        # seven characters even when it truncates the middle.
        assert self.SECRET[-7:] not in message
        assert "DISTINCTIVETAIL" not in message

    def test_no_input_is_echoed_at_all(self, tmp_path):
        """The structural property, not a denylist of things we thought of."""
        message = self._load_expecting_failure(
            tmp_path,
            f'storage:\n  backend: azure_blob\n  azure_blob:\n    connection_string: "{self.SECRET}"\n',
        )
        assert "input_value" not in message
        assert "input_type" not in message

    def test_the_offending_key_is_still_named(self, tmp_path):
        """Dropping the input must not cost diagnostic value.

        Pydantic puts the offending key in `loc`, so "which key" and "what is
        wrong with it" both survive without echoing the value.
        """
        message = self._load_expecting_failure(tmp_path, "storage:\n  backend: local\n  definitely_not_a_key: 1\n")
        assert "definitely_not_a_key" in message
        assert "not permitted" in message

    def test_the_config_path_is_still_named(self, tmp_path):
        """With multi-file merge, which file owns the bad key is half the answer."""
        path = self._write(tmp_path, "storage:\n  backend: local\n  nope: 1\n")
        with pytest.raises(ConfigError) as caught:
            load_config(str(path))
        assert str(path) in str(caught.value)
