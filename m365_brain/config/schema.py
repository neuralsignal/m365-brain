"""Config schema -- the one Pydantic root, and the sections that are not big
enough to earn a module of their own.

The tree is deliberately one root: `Config` is the only thing a consumer loads,
and every subsystem reads its own section off it. There is no second config
system, no env-var discovery, and no value invented in code.

Section-level optionality: the new subsystem sections (`index`, `vault`,
`outboxes`, `hooks`, `manifest`, `m365`, `ops`) are `| None`, matching `web`
and `worker`, which have always been. The reason is structural rather than
convenient -- the index half and the Microsoft 365 half do not know about each
other, so a config that indexes a folder of markdown and never talks to Graph
is a legitimate config, and so is the reverse. Absence means "this subsystem is
not in use"; `require_section()` turns using it anyway into a named crash.
Inside a section, every field is required.

Every secret in the tree is `SecretStr`, never `str`. That makes redaction a
property of the type rather than of a list of field names kept somewhere else:
`repr`, `str`, `model_dump` and `model_dump_json` all render `**********`, so
`config show`, a log line and an exception message are safe by construction and
not by anybody remembering. The raw value is reachable only through an explicit
`.get_secret_value()`, which is what makes the handful of places that genuinely
need it greppable. Add a new secret as `SecretStr` and it is covered with no
other edit -- `tests/unit/test_config_secrets.py` walks the annotations.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, SecretStr, model_validator

from m365_brain.config.base import SECTION_MODEL_CONFIG
from m365_brain.config.extractors import ExtractorsConfig
from m365_brain.config.index import IndexConfig
from m365_brain.config.ops import OpsConfig
from m365_brain.config.outbox import OutboxesConfig
from m365_brain.config.runtime import HooksConfig, M365Config, ManifestConfig
from m365_brain.config.vault import VaultConfig


class AuthProfileConfig(BaseModel):
    """One Entra app registration.

    The unit `auth.profiles` maps names to. Three apps run side by side in a
    typical deployment -- one for mail, one for files, one for chat posting --
    because their scopes must not be pooled.
    """

    model_config = SECTION_MODEL_CONFIG
    client_id: str
    tenant_id: str
    scopes: list[str]
    token_cache_path: str
    client_secret: SecretStr | None
    """`null` for a public client (device-code flow), a string for a
    confidential one (auth-code flow). Required either way: the flow is chosen
    by this field, so it is never inferred. Presence -- not the value -- is what
    selects the flow, so the only reader that unwraps it is the MSAL call."""


class AuthConfig(AuthProfileConfig):
    """The `auth:` section -- the profile the extractor path uses today, plus
    the named-profile registry.

    `client_secret` keeps a `None` default here and only here: every existing
    config file omits it for the device-code flow, and `AuthProfileConfig`
    (which `profiles` uses) has no default at all.
    """

    model_config = SECTION_MODEL_CONFIG
    client_secret: SecretStr | None = None
    profiles: dict[str, AuthProfileConfig] | None = None
    """Named Entra apps, addressed by `outboxes.definitions.<name>.auth_profile`
    and `extractors.auth_profile`. `None` means one app -- the `auth:` section
    itself -- which is the single-tenant single-purpose deployment."""


class ServiceConfig(BaseModel):
    model_config = SECTION_MODEL_CONFIG
    mode: str
    log_level: str
    json_logs: bool
    continuous_poll_seconds: int
    max_consecutive_auth_failures: int


class LocalStorageConfig(BaseModel):
    model_config = SECTION_MODEL_CONFIG
    base_path: str


class AzureBlobStorageConfig(BaseModel):
    model_config = SECTION_MODEL_CONFIG
    connection_string: SecretStr
    container_name: str
    prefix: str


class StorageConfig(BaseModel):
    model_config = SECTION_MODEL_CONFIG
    backend: str
    local: LocalStorageConfig | None = None
    azure_blob: AzureBlobStorageConfig | None = None


class GraphConfig(BaseModel):
    model_config = SECTION_MODEL_CONFIG
    max_retries: int
    backoff_base_ms: int
    timeout_seconds: int
    max_pages: int
    max_retry_after_seconds: float
    error_message_max_length: int


class MediaConfig(BaseModel):
    model_config = SECTION_MODEL_CONFIG
    extract_images: bool
    image_format: str
    image_max_dimension: int


class ExtractionConfig(BaseModel):
    model_config = SECTION_MODEL_CONFIG
    timeout_seconds: int
    max_file_size_mb: int
    xlsx_max_rows_per_sheet: int
    isolation: Literal["thread", "process"]


class ConvertersConfig(BaseModel):
    model_config = SECTION_MODEL_CONFIG
    backends: dict[str, str]
    extraction: ExtractionConfig
    media: MediaConfig | None = None
    slug_max_length: int
    hash_length: int


class WebConfig(BaseModel):
    model_config = SECTION_MODEL_CONFIG
    host: str
    port: int
    secret_key: SecretStr
    fernet_key: SecretStr
    db_path: str
    session_timeout_minutes: int
    db_url: str
    admin_emails: list[str]


class WorkerConfig(BaseModel):
    model_config = SECTION_MODEL_CONFIG
    max_concurrent_jobs: int
    poll_interval_seconds: int


class Config(BaseModel):
    model_config = SECTION_MODEL_CONFIG
    auth: AuthConfig
    service: ServiceConfig
    storage: StorageConfig
    graph: GraphConfig
    extractors: ExtractorsConfig
    converters: ConvertersConfig
    web: WebConfig | None = None
    worker: WorkerConfig | None = None
    index: IndexConfig | None = None
    vault: VaultConfig | None = None
    outboxes: OutboxesConfig | None = None
    hooks: HooksConfig | None = None
    manifest: ManifestConfig | None = None
    m365: M365Config | None = None
    ops: OpsConfig | None = None

    @model_validator(mode="after")
    def _named_auth_profiles_resolve(self) -> Config:
        """Every profile name referenced elsewhere exists in `auth.profiles`.

        A name that does not resolve is a config error at load, not a
        KeyError four hours into a run.
        """
        available = set(self.auth.profiles or {})
        referenced: list[tuple[str, str]] = []
        if self.extractors.auth_profile is not None:
            referenced.append(("extractors.auth_profile", self.extractors.auth_profile))
        if self.outboxes is not None:
            referenced.extend(
                (f"outboxes.definitions.{name}.auth_profile", definition.auth_profile)
                for name, definition in self.outboxes.definitions.items()
            )
        unknown = [(where, name) for where, name in referenced if name not in available]
        if unknown:
            raise ValueError(
                "auth profile names must exist in auth.profiles "
                f"(configured: {sorted(available)}) -- unresolved: {unknown}"
            )
        return self
