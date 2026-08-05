"""Tests for the subsystem config sections: index, vault, outboxes, hooks,
manifest, m365 and ops, plus the auth-profile registry.

The point of these tests is not that Pydantic works. It is that the four
promises the config root makes are actually kept: nothing has a default, a
typo fails loudly, `${VAR}` still expands, and a relative path still resolves
against the config file rather than whatever directory the process happens to
be in.
"""

from __future__ import annotations

import copy
import os
from pathlib import Path

import pytest
import yaml
from hypothesis import assume, given
from hypothesis import strategies as st

from m365_brain.config import (
    Config,
    ConfigError,
    is_hook_spec,
    load_config,
    require_section,
)

EXAMPLE_CONFIG = Path(__file__).resolve().parents[2] / "m365_brain" / "templates" / "m365-brain.yaml"

EXAMPLE_ENV = {
    "MSAL_CLIENT_ID": "example-client",
    "MSAL_TENANT_ID": "example-tenant",
    "M365_MAIL_CLIENT_ID": "example-mail-client",
    "M365_FILES_CLIENT_ID": "example-files-client",
    "M365_CHAT_CLIENT_ID": "example-chat-client",
    "M365_OWN_EMAIL": "person@example.com",
}


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _legacy_sections() -> dict:
    """The sections that predate this work, at their minimum valid shape."""
    return {
        "auth": {
            "client_id": "id",
            "tenant_id": "tenant",
            "scopes": ["User.Read"],
            "token_cache_path": "./state/token.json",
            "client_secret": None,
        },
        "service": {
            "mode": "cli",
            "log_level": "INFO",
            "json_logs": False,
            "continuous_poll_seconds": 30,
            "max_consecutive_auth_failures": 5,
        },
        "storage": {"backend": "local", "local": {"base_path": "./vault"}},
        "graph": {
            "max_retries": 3,
            "backoff_base_ms": 2000,
            "timeout_seconds": 30,
            "max_pages": 100,
            "max_retry_after_seconds": 300.0,
            "error_message_max_length": 200,
        },
        "extractors": {
            "email": {
                "enabled": False,
                "poll_interval_minutes": 3,
                "mailboxes": [{"address": "me", "folders": ["Inbox"], "output_subdir": ""}],
                "lookback_days": 30,
                "max_items_per_sync": 10,
                "download_attachments": False,
                "max_attachment_size_mb": 25,
                "attachment_convert_extensions": [],
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
                "download_attachments": False,
                "download_inline_images": False,
                "max_attachment_size_mb": 25,
                "attachment_convert_extensions": [],
            },
            "teams_channels": {
                "enabled": False,
                "poll_interval_minutes": 5,
                "max_messages_per_channel": 200,
                "channels": None,
                "download_attachments": False,
                "download_inline_images": False,
                "max_attachment_size_mb": 25,
                "attachment_convert_extensions": [],
            },
            "onedrive": {
                "enabled": False,
                "poll_interval_minutes": 120,
                "eager_convert_patterns": [],
                "convertible_extensions": [".md"],
                "max_file_size_mb": 100,
            },
            "sharepoint": {
                "enabled": False,
                "poll_interval_minutes": 240,
                "eager_convert_patterns": [],
                "convertible_extensions": [".md"],
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
                "isolation": "thread",
            },
            "slug_max_length": 80,
            "hash_length": 6,
        },
    }


def _index_section() -> dict:
    return {
        "backend": "sqlite",
        "sqlite": {"path": "./index.db", "busy_timeout_ms": 30000, "journal_mode": "WAL"},
        "roots": [
            {"name": "vault", "path": "./vault", "recursive": True},
            {"name": "notes", "path": "./notes", "recursive": True},
        ],
        "file_extensions": [".md"],
        "exclude": ["**/_meta/**"],
        "sync": {"batch_size": 200, "interval_minutes": 60},
        "frontmatter": {
            "title_key": "title",
            "type_key": "type",
            "permalink_key": "permalink",
            "tags_key": "tags",
            "aliases_key": "aliases",
            "default_type": "note",
            "structural_keys": ["title", "type", "permalink", "tags"],
        },
        "observations": {"default_category": "Note"},
        "relations": {"explicit_default_type": "relates_to", "inline_type": "links_to"},
        "search": {
            "page_size": 20,
            "bm25_weights": {"title": 10.0, "content": 1.0, "tags": 5.0},
            "snippet": {
                "column": "content",
                "start_marker": ">>>",
                "end_marker": "<<<",
                "ellipsis": "...",
                "max_tokens": 40,
            },
            "rrf_k": 60,
            "rrf_min_weight": 0.1,
            "vector_candidates": 100,
            "min_similarity": 0.55,
        },
        "catalog": {
            "conversion_states": ["pending", "converted", "failed"],
            "initial_state": "pending",
            "converted_state": "converted",
            "failed_state": "failed",
        },
        "vector": {
            "enabled": True,
            "provider": "hash",
            "store": "memory",
            "model": "test-model",
            "dimensions": 8,
            "threads": 1,
            "chunk_size": 900,
            "chunk_overlap": 120,
            "embed_batch_size": 32,
            "write_batch_size": 50,
        },
    }


def _vault_section() -> dict:
    return {
        "root": "./vault",
        "layout": {
            "inbox": "inbox",
            "annotations": "annotations",
            "outbox": "outbox",
            "meta": "_meta",
            "processed": "_processed",
            "rejected": "_rejected",
            "inflight": "_inflight",
            "state": "state",
            "manifests": "manifests",
        },
        "extractor_dirs": {
            "email": "emails",
            "calendar": "calendar",
            "contacts": "contacts",
            "directory": "directory",
            "onedrive": "onedrive",
            "sharepoint": "sharepoint",
            "teams_chats": "teams-chats",
            "teams_channels": "teams-channels",
        },
        "filenames": {
            "entry": "index.md",
            "conversation": "messages.md",
            "conversation_store": "messages.jsonl",
            "attachments": "attachments",
            "attachments_converted": "attachments_converted",
        },
    }


def _outboxes_section() -> dict:
    return {
        "attachment_root": "./attachments",
        "forbidden_send_scopes": ["Mail.Send"],
        "definitions": {"email.draft": {"tier": "draft_only", "auth_profile": "mail"}},
        "email": {
            "signature": {"html_path": None, "logo_path": None, "logo_content_id": "logo"},
        },
        "reconcile": {"quote_markers": ["^From:"]},
    }


def _ops_section() -> dict:
    return {
        "link_resolution": {"unresolved_prefix": "contact-", "target_type": "person"},
        "tiers": {
            "lookback_days": 90,
            "ladder": [
                {"name": "close", "min_per_month": 4.0, "stale_after_days": 14},
                {"name": "rest", "min_per_month": 0.0, "stale_after_days": None},
            ],
            "interaction_sources": [
                {
                    "entity_type": "email",
                    "party_from": {"observation": "from", "relation": None},
                    "timestamp": {"observation": "received_at"},
                    "exclude_future": True,
                }
            ],
            "write_back": {"enabled": False, "fields": {"tier": "tier"}, "create_missing": False},
        },
        "triage": {
            "own_email": "person@example.com",
            "inbox_folder": "Inbox",
            "sent_folders": ["SentItems"],
            "forward_prefixes": ["fw:"],
            "fields": {
                "entity_type": "email",
                "folder": "folder",
                "conversation_id": "conversation_id",
                "message_id": "message_id",
                "sender": "sender",
                "recipients": "to",
                "timestamp": "date",
            },
        },
    }


def _profiles() -> dict:
    return {
        "mail": {
            "client_id": "mail-id",
            "tenant_id": "tenant",
            "scopes": ["Mail.ReadWrite"],
            "token_cache_path": "./state/mail.json",
            "client_secret": None,
        }
    }


def full_payload() -> dict:
    """A config exercising every section."""
    payload = _legacy_sections()
    payload["auth"]["profiles"] = _profiles()
    payload["extractors"]["auth_profile"] = "mail"
    payload["index"] = _index_section()
    payload["vault"] = _vault_section()
    payload["outboxes"] = _outboxes_section()
    payload["hooks"] = {"post_cycle": ["my_package.hooks:on_cycle"], "post_reconcile": []}
    payload["manifest"] = {"retain_cycles": 50, "latest_filename": "latest.json"}
    payload["m365"] = {
        "upload": {
            "inline_attachment_max_bytes": 2250000,
            "simple_upload_max_bytes": 4194304,
            "chunk_bytes": 4259840,
        }
    }
    payload["ops"] = _ops_section()
    return payload


def write_config(directory: Path, payload: dict, name: str = "config.yaml") -> Path:
    path = directory / name
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")
    return path


def load(directory: Path, payload: dict) -> Config:
    return load_config(str(write_config(directory, payload)))


# ---------------------------------------------------------------------------
# The whole tree round-trips
# ---------------------------------------------------------------------------


class TestCompleteConfig:
    def test_every_section_loads(self, tmp_path):
        config = load(tmp_path, full_payload())

        assert config.index is not None
        assert config.vault is not None
        assert config.outboxes is not None
        assert config.hooks is not None
        assert config.manifest is not None
        assert config.m365 is not None
        assert config.ops is not None

    def test_index_values_round_trip(self, tmp_path):
        index = load(tmp_path, full_payload()).index

        assert index.backend == "sqlite"
        assert index.sqlite.busy_timeout_ms == 30000
        assert index.sqlite.journal_mode == "WAL"
        assert index.sync.batch_size == 200
        assert index.sync.interval_minutes == 60
        assert index.frontmatter.default_type == "note"
        assert index.frontmatter.structural_keys == ["title", "type", "permalink", "tags"]
        assert index.observations.default_category == "Note"
        assert index.relations.explicit_default_type == "relates_to"
        assert index.relations.inline_type == "links_to"
        assert index.search.bm25_weights.title == 10.0
        assert index.search.snippet.start_marker == ">>>"
        assert index.search.rrf_k == 60
        assert index.search.min_similarity == 0.55
        assert index.catalog.initial_state == "pending"
        assert index.vector.dimensions == 8
        assert index.vector.chunk_size == 900
        assert index.vector.chunk_overlap == 120
        assert index.vector.write_batch_size == 50

    def test_vault_names_round_trip(self, tmp_path):
        vault = load(tmp_path, full_payload()).vault

        assert vault.layout.meta == "_meta"
        assert vault.layout.inflight == "_inflight"
        assert vault.extractor_dirs["teams_chats"] == "teams-chats"
        assert vault.filenames.entry == "index.md"
        assert vault.filenames.conversation_store == "messages.jsonl"

    def test_outbox_tier_round_trips(self, tmp_path):
        outboxes = load(tmp_path, full_payload()).outboxes

        assert outboxes.definitions["email.draft"].tier == "draft_only"
        assert outboxes.definitions["email.draft"].auth_profile == "mail"
        assert outboxes.email.signature.html_path is None

    def test_frozen(self, tmp_path):
        config = load(tmp_path, full_payload())
        with pytest.raises(Exception, match="frozen|Instance is frozen"):
            config.index.sqlite.busy_timeout_ms = 1

    def test_example_file_loads(self, monkeypatch):
        """The shipped example is the first documentation a stranger reads.

        If it does not parse, it is worse than no documentation.
        """
        for key, value in EXAMPLE_ENV.items():
            monkeypatch.setenv(key, value)
        config = load_config(str(EXAMPLE_CONFIG))
        assert config.index is not None
        assert config.vault is not None
        assert config.outboxes is not None
        assert config.ops is not None
        assert sorted(config.auth.profiles) == ["chat", "files", "mail"]


# ---------------------------------------------------------------------------
# Missing and unknown keys
# ---------------------------------------------------------------------------


class TestMissingAndUnknownKeys:
    @pytest.mark.parametrize(
        ("section", "key"),
        [
            ("index", "backend"),
            ("index", "file_extensions"),
            ("vault", "root"),
            ("vault", "filenames"),
            ("outboxes", "attachment_root"),
            ("manifest", "retain_cycles"),
            ("ops", "triage"),
        ],
    )
    def test_missing_key_names_the_key(self, tmp_path, section, key):
        payload = full_payload()
        del payload[section][key]

        with pytest.raises(ConfigError) as exc:
            load(tmp_path, payload)

        assert f"{section}.{key}" in str(exc.value)

    def test_missing_nested_key_names_the_full_path(self, tmp_path):
        payload = full_payload()
        del payload["index"]["vector"]["dimensions"]

        with pytest.raises(ConfigError, match=r"index\.vector\.dimensions"):
            load(tmp_path, payload)

    @pytest.mark.parametrize(
        "field",
        ["entity_type", "folder", "conversation_id", "message_id", "sender", "recipients", "timestamp"],
    )
    def test_a_triage_category_has_no_default(self, tmp_path, field):
        """Seven names, none of them guessable.

        A default here would not fail -- it would produce an empty report, which
        reads exactly like an inbox with nothing in it.
        """
        payload = full_payload()
        del payload["ops"]["triage"]["fields"][field]

        with pytest.raises(ConfigError, match=rf"ops\.triage\.fields\.{field}"):
            load(tmp_path, payload)

    def test_error_message_names_the_config_file(self, tmp_path):
        payload = full_payload()
        del payload["index"]["backend"]

        with pytest.raises(ConfigError) as exc:
            load(tmp_path, payload)

        assert "config.yaml" in str(exc.value)

    @pytest.mark.parametrize(
        "section",
        ["index", "vault", "outboxes", "hooks", "manifest", "ops"],
    )
    def test_unknown_key_is_rejected(self, tmp_path, section):
        payload = full_payload()
        payload[section]["definitely_not_a_key"] = 1

        with pytest.raises(ConfigError, match="definitely_not_a_key"):
            load(tmp_path, payload)

    def test_unknown_nested_key_is_rejected(self, tmp_path):
        payload = full_payload()
        payload["index"]["search"]["snippet"]["colour"] = "red"

        with pytest.raises(ConfigError, match="colour"):
            load(tmp_path, payload)

    def test_absent_section_is_none_not_a_default(self, tmp_path):
        """Omitting a section means "not in use" -- it does not conjure values."""
        payload = _legacy_sections()
        config = load(tmp_path, payload)

        assert config.index is None
        assert config.vault is None
        assert config.outboxes is None
        assert config.ops is None


# ---------------------------------------------------------------------------
# Env expansion and path resolution
# ---------------------------------------------------------------------------


class TestExpansionAndPaths:
    def test_env_expansion_inside_index(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TEST_INDEX_DB", "expanded-index.db")
        payload = full_payload()
        payload["index"]["sqlite"]["path"] = "./${TEST_INDEX_DB}"

        config = load(tmp_path, payload)

        assert config.index.sqlite.path.endswith("expanded-index.db")

    def test_env_expansion_inside_a_profile(self, tmp_path, monkeypatch):
        monkeypatch.setenv("TEST_MAIL_CLIENT", "mail-client-from-env")
        payload = full_payload()
        payload["auth"]["profiles"]["mail"]["client_id"] = "${TEST_MAIL_CLIENT}"

        config = load(tmp_path, payload)

        assert config.auth.profiles["mail"].client_id == "mail-client-from-env"

    def test_unset_env_var_crashes(self, tmp_path, monkeypatch):
        monkeypatch.delenv("TEST_UNSET_INDEX_VAR", raising=False)
        payload = full_payload()
        payload["index"]["sqlite"]["path"] = "./${TEST_UNSET_INDEX_VAR}"

        with pytest.raises(ConfigError, match="TEST_UNSET_INDEX_VAR"):
            load(tmp_path, payload)

    def test_paths_resolve_against_the_config_file_not_the_cwd(self, tmp_path, monkeypatch):
        config_dir = tmp_path / "conf"
        config_dir.mkdir()
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        path = write_config(config_dir, full_payload())

        monkeypatch.chdir(elsewhere)
        config = load_config(str(path))

        assert config.index.sqlite.path == str((config_dir / "index.db").resolve())
        assert config.vault.root == str((config_dir / "vault").resolve())
        assert config.outboxes.attachment_root == str((config_dir / "attachments").resolve())
        assert str(elsewhere) not in config.index.sqlite.path

    def test_root_paths_resolve_against_the_config_file(self, tmp_path, monkeypatch):
        config_dir = tmp_path / "conf"
        config_dir.mkdir()
        path = write_config(config_dir, full_payload())

        monkeypatch.chdir(tmp_path)
        config = load_config(str(path))

        assert [root.path for root in config.index.roots] == [
            str((config_dir / "vault").resolve()),
            str((config_dir / "notes").resolve()),
        ]

    def test_absolute_paths_are_left_alone(self, tmp_path):
        absolute = str(tmp_path / "absolute" / "index.db")
        payload = full_payload()
        payload["index"]["sqlite"]["path"] = absolute

        assert load(tmp_path, payload).index.sqlite.path == absolute

    def test_null_signature_paths_survive_resolution(self, tmp_path):
        """`html_path: null` must stay null, not become the config directory."""
        config = load(tmp_path, full_payload())

        assert config.outboxes.email.signature.html_path is None
        assert config.outboxes.email.signature.logo_path is None


# ---------------------------------------------------------------------------
# Index roots
# ---------------------------------------------------------------------------


class TestIndexRoots:
    def test_two_roots_with_the_same_relative_path_are_distinguishable(self, tmp_path):
        """The bug this prevents: two roots each holding `projects/x.md`.

        Entity keys are `{name}/{relative path}`, so the names must survive
        loading intact and must not be collapsed or deduplicated.
        """
        payload = full_payload()
        payload["index"]["roots"] = [
            {"name": "personal", "path": "./trees/a", "recursive": True},
            {"name": "shared", "path": "./trees/b", "recursive": True},
        ]

        roots = load(tmp_path, payload).index.roots

        assert [root.name for root in roots] == ["personal", "shared"]
        assert roots[0].path != roots[1].path
        keys = {f"{root.name}/projects/x.md" for root in roots}
        assert len(keys) == 2

    def test_duplicate_root_names_are_rejected(self, tmp_path):
        payload = full_payload()
        payload["index"]["roots"] = [
            {"name": "same", "path": "./a", "recursive": True},
            {"name": "same", "path": "./b", "recursive": True},
        ]

        with pytest.raises(ConfigError, match="unique"):
            load(tmp_path, payload)

    def test_empty_roots_are_rejected(self, tmp_path):
        payload = full_payload()
        payload["index"]["roots"] = []

        with pytest.raises(ConfigError, match="at least one root"):
            load(tmp_path, payload)


# ---------------------------------------------------------------------------
# Cross-field validators
# ---------------------------------------------------------------------------


class TestValidators:
    def test_initial_state_must_be_a_configured_state(self, tmp_path):
        payload = full_payload()
        payload["index"]["catalog"]["initial_state"] = "queued"

        with pytest.raises(ConfigError, match="conversion_states"):
            load(tmp_path, payload)

    def test_chunk_overlap_must_be_below_chunk_size(self, tmp_path):
        payload = full_payload()
        payload["index"]["vector"]["chunk_overlap"] = 900

        with pytest.raises(ConfigError, match="chunk_overlap"):
            load(tmp_path, payload)

    def test_unknown_backend_is_rejected(self, tmp_path):
        payload = full_payload()
        payload["index"]["backend"] = "postgres"

        with pytest.raises(ConfigError, match="backend"):
            load(tmp_path, payload)

    def test_extractor_dirs_must_cover_every_extractor(self, tmp_path):
        payload = full_payload()
        del payload["vault"]["extractor_dirs"]["calendar"]

        with pytest.raises(ConfigError, match="calendar"):
            load(tmp_path, payload)

    def test_extractor_dirs_rejects_an_unknown_extractor(self, tmp_path):
        payload = full_payload()
        payload["vault"]["extractor_dirs"]["telepathy"] = "telepathy"

        with pytest.raises(ConfigError, match="telepathy"):
            load(tmp_path, payload)

    def test_unknown_tier_is_rejected(self, tmp_path):
        payload = full_payload()
        payload["outboxes"]["definitions"]["email.draft"]["tier"] = "probably_fine"

        with pytest.raises(ConfigError, match="tier"):
            load(tmp_path, payload)

    def test_empty_outbox_definitions_are_rejected(self, tmp_path):
        payload = full_payload()
        payload["outboxes"]["definitions"] = {}

        with pytest.raises(ConfigError, match="at least one outbox"):
            load(tmp_path, payload)

    def test_upload_chunk_must_be_a_graph_multiple(self, tmp_path):
        payload = full_payload()
        payload["m365"]["upload"]["chunk_bytes"] = 4194304  # 4 MiB is not a multiple of 320 KiB

        with pytest.raises(ConfigError, match="320 KiB"):
            load(tmp_path, payload)

    def test_party_from_needs_exactly_one_source(self, tmp_path):
        payload = full_payload()
        payload["ops"]["tiers"]["interaction_sources"][0]["party_from"] = {
            "observation": "from",
            "relation": "attended_by",
        }

        with pytest.raises(ConfigError, match="exactly one"):
            load(tmp_path, payload)

    def test_empty_tier_ladder_is_rejected(self, tmp_path):
        payload = full_payload()
        payload["ops"]["tiers"]["ladder"] = []

        with pytest.raises(ConfigError, match="at least one rung"):
            load(tmp_path, payload)


# ---------------------------------------------------------------------------
# Auth profiles
# ---------------------------------------------------------------------------


class TestAuthProfiles:
    def test_profiles_load(self, tmp_path):
        config = load(tmp_path, full_payload())

        assert config.auth.profiles["mail"].client_id == "mail-id"
        assert config.auth.profiles["mail"].client_secret is None

    def test_a_profile_must_state_its_client_secret(self, tmp_path):
        payload = full_payload()
        del payload["auth"]["profiles"]["mail"]["client_secret"]

        with pytest.raises(ConfigError, match="client_secret"):
            load(tmp_path, payload)

    def test_unknown_extractor_profile_is_rejected(self, tmp_path):
        payload = full_payload()
        payload["extractors"]["auth_profile"] = "nonexistent"

        with pytest.raises(ConfigError, match="nonexistent"):
            load(tmp_path, payload)

    def test_unknown_outbox_profile_is_rejected(self, tmp_path):
        payload = full_payload()
        payload["outboxes"]["definitions"]["email.draft"]["auth_profile"] = "nonexistent"

        with pytest.raises(ConfigError, match="nonexistent"):
            load(tmp_path, payload)

    def test_no_profiles_is_the_single_app_path(self, tmp_path):
        payload = full_payload()
        del payload["auth"]["profiles"]
        del payload["extractors"]["auth_profile"]
        del payload["outboxes"]

        config = load(tmp_path, payload)

        assert config.auth.profiles is None
        assert config.extractors.auth_profile is None


# ---------------------------------------------------------------------------
# Hooks
# ---------------------------------------------------------------------------


class TestHooks:
    def test_valid_specs_load(self, tmp_path):
        payload = full_payload()
        payload["hooks"] = {
            "post_cycle": ["pkg.mod:fn", "pkg:fn"],
            "post_reconcile": ["a.b.c.d:e"],
        }

        hooks = load(tmp_path, payload).hooks

        assert hooks.post_cycle == ["pkg.mod:fn", "pkg:fn"]
        assert hooks.post_reconcile == ["a.b.c.d:e"]

    @pytest.mark.parametrize("spec", ["pkg.mod.fn", "pkg.mod:", ":fn", "pkg mod:fn", "1pkg:fn", ""])
    def test_malformed_specs_are_rejected(self, tmp_path, spec):
        payload = full_payload()
        payload["hooks"]["post_cycle"] = [spec]

        with pytest.raises(ConfigError, match="module.path:callable"):
            load(tmp_path, payload)

    def test_config_parsing_does_not_import_the_hook(self, tmp_path):
        """A shape check, not a resolution: parsing config must stay pure."""
        payload = full_payload()
        payload["hooks"]["post_cycle"] = ["no_such_module_anywhere:handler"]

        assert load(tmp_path, payload).hooks.post_cycle == ["no_such_module_anywhere:handler"]


_IDENTIFIER = st.from_regex(r"\A[A-Za-z_][A-Za-z0-9_]{0,8}\Z")


class TestHookSpecProperties:
    @given(modules=st.lists(_IDENTIFIER, min_size=1, max_size=4), attribute=_IDENTIFIER)
    def test_wellformed_specs_are_accepted(self, modules, attribute):
        assert is_hook_spec(f"{'.'.join(modules)}:{attribute}")

    @given(text=st.text())
    def test_a_spec_without_a_colon_is_rejected(self, text):
        assume(":" not in text)
        assert not is_hook_spec(text)

    @given(text=st.text())
    def test_never_raises(self, text):
        assert is_hook_spec(text) in (True, False)


# ---------------------------------------------------------------------------
# require_section
# ---------------------------------------------------------------------------


class TestRequireSection:
    def test_returns_the_section_when_present(self, tmp_path):
        config = load(tmp_path, full_payload())

        assert require_section(config.index, "index") is config.index

    def test_raises_naming_the_section(self, tmp_path):
        config = load(tmp_path, _legacy_sections())

        with pytest.raises(ConfigError, match="index"):
            require_section(config.index, "index")


# ---------------------------------------------------------------------------
# Multi-file merge still works with the new sections
# ---------------------------------------------------------------------------


class TestMerge:
    def test_a_later_file_overrides_one_index_key(self, tmp_path):
        base = write_config(tmp_path, full_payload(), name="base.yaml")
        override = write_config(
            tmp_path,
            {"index": {"sqlite": {"busy_timeout_ms": 1000}}},
            name="override.yaml",
        )

        config = load_config(f"{base},{override}")

        assert config.index.sqlite.busy_timeout_ms == 1000
        assert config.index.sqlite.journal_mode == "WAL"

    def test_a_later_file_replaces_the_root_list_wholesale(self, tmp_path):
        base = write_config(tmp_path, full_payload(), name="base.yaml")
        override = write_config(
            tmp_path,
            {"index": {"roots": [{"name": "only", "path": "./only", "recursive": False}]}},
            name="override.yaml",
        )

        config = load_config(f"{base},{override}")

        assert [root.name for root in config.index.roots] == ["only"]

    def test_payload_builder_is_not_shared_between_tests(self):
        """Guard on the fixtures themselves: a mutated builder result would
        make every test above depend on execution order."""
        first = full_payload()
        second = full_payload()
        first["index"]["roots"].clear()
        assert second["index"]["roots"]

    def test_deepcopy_of_a_payload_is_equal(self):
        payload = full_payload()
        assert copy.deepcopy(payload) == payload


def test_example_config_exists():
    assert EXAMPLE_CONFIG.is_file(), f"{EXAMPLE_CONFIG} is the documented starting point"
    assert os.path.getsize(EXAMPLE_CONFIG) > 0
