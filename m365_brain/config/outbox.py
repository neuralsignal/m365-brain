"""The `outboxes:` section -- authority policy, signature, reconciliation markers.

An authority is policy, so it is config rather than a class attribute on a
handler. It is not called `tier`: `ops.tiers` spends that word on a person's
relationship rung, which is written into person files and read back by name --
see `m365_brain/outbox/authority.py`.
The quote-marker table is locale- and sign-off-specific, so it is config rather
than a regex list in a module: a user's own closing phrase compiled into a
library is exactly the assumption this package exists to remove.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, model_validator

from m365_brain.config.base import SECTION_MODEL_CONFIG

Authority = Literal["never_auto", "human_approval", "draft_only", "auto_send"]


class OutboxDefinitionConfig(BaseModel):
    """One outbox: its permission level and the Entra app it dispatches through."""

    model_config = SECTION_MODEL_CONFIG
    authority: Authority
    auth_profile: str


class EmailSignatureConfig(BaseModel):
    model_config = SECTION_MODEL_CONFIG
    html_path: str | None
    """Signature HTML appended to every outgoing body.

    `null` is meaningful and is a required spelling: an operator with no
    signature states so, rather than the library inferring it from a missing
    file at dispatch time.
    """

    logo_path: str | None
    """Image attached inline and referenced by `logo_content_id`.

    `null` is meaningful for the same reason, and independently of
    `html_path`: a text-only signature is a normal configuration.
    """

    logo_content_id: str


class EmailOutboxConfig(BaseModel):
    model_config = SECTION_MODEL_CONFIG
    signature: EmailSignatureConfig


class ReconcileConfig(BaseModel):
    model_config = SECTION_MODEL_CONFIG
    quote_markers: list[str]


class OutboxesConfig(BaseModel):
    model_config = SECTION_MODEL_CONFIG
    attachment_root: str
    forbidden_send_scopes: list[str]
    definitions: dict[str, OutboxDefinitionConfig]
    email: EmailOutboxConfig
    reconcile: ReconcileConfig

    @model_validator(mode="after")
    def _definitions_not_empty(self) -> OutboxesConfig:
        if not self.definitions:
            raise ValueError(
                "outboxes.definitions must name at least one outbox; an outboxes section "
                "with no outbox cannot dispatch anything"
            )
        return self
