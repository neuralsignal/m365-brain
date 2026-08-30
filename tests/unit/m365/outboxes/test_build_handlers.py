"""Tests for build_handlers error paths in m365_brain.m365.outboxes.__init__."""

from __future__ import annotations

import pytest

from m365_brain.config import (
    EmailOutboxConfig,
    EmailSignatureConfig,
    OutboxDefinitionConfig,
    OutboxesConfig,
    ReconcileConfig,
    UploadConfig,
)
from m365_brain.m365.outboxes import build_handlers


@pytest.fixture()
def upload():
    return UploadConfig(
        inline_attachment_max_bytes=2_250_000,
        simple_upload_max_bytes=4 * 1024 * 1024,
        chunk_bytes=320 * 1024 * 12,
    )


@pytest.fixture()
def signature():
    return EmailSignatureConfig(
        html_path=None,
        logo_path="logo.png",
        logo_content_id="brand_logo",
    )


def _make_config(
    attachment_root: str,
    signature: EmailSignatureConfig,
    definitions: dict[str, OutboxDefinitionConfig],
) -> OutboxesConfig:
    return OutboxesConfig(
        attachment_root=attachment_root,
        forbidden_send_scopes=["Mail.Send"],
        definitions=definitions,
        email=EmailOutboxConfig(signature=signature),
        reconcile=ReconcileConfig(quote_markers=[r"^\s*From:\s"]),
    )


def test_build_handlers_missing_client(tmp_path, upload, signature, client):
    config = _make_config(
        attachment_root=str(tmp_path),
        signature=signature,
        definitions={
            "email.draft": OutboxDefinitionConfig(
                authority="draft_only",
                auth_profile="nonexistent_profile",
            ),
        },
    )
    with pytest.raises(KeyError, match="nonexistent_profile"):
        build_handlers(config, upload, clients={})


def test_handler_unknown_kind(tmp_path, upload, signature, client):
    config = _make_config(
        attachment_root=str(tmp_path),
        signature=signature,
        definitions={
            "carrier.pigeon": OutboxDefinitionConfig(
                authority="draft_only",
                auth_profile="main",
            ),
        },
    )
    with pytest.raises(KeyError, match="carrier.pigeon"):
        build_handlers(config, upload, clients={"main": client})
