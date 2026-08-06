"""Fixtures for the Microsoft 365 executors.

Every test drives a real `GraphClient` against a respx catch-all so the whole
transport -- retry policy, header construction, JSON encoding -- is in the
path. Mocking the client instead would test the handler against a shape nobody
sends.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import httpx
import pytest
import respx

from m365_brain.config import (
    EmailOutboxConfig,
    EmailSignatureConfig,
    GraphConfig,
    OutboxDefinitionConfig,
    OutboxesConfig,
    ReconcileConfig,
    UploadConfig,
)
from m365_brain.m365.client import GraphClient
from m365_brain.vault.intent import parse_intent

TOKEN = "test-token"
ASSETS = Path(__file__).resolve().parents[3] / "fixtures" / "outbox" / "assets"

SIGNATURE_HTML = "<p>--<br>Test Sender</p><img src='cid:brand_logo'>"
LOGO_CONTENT_ID = "brand_logo"

# Must exceed the inline-attachment ceiling so the upload-session path is
# reached. Generated rather than committed -- 2.3 MB of filler in git buys
# nothing that a deterministic generator does not.
LARGE_ATTACHMENT_BYTES = bytes(range(256)) * 9000

BODY = "# Heading\n\nHello **there**.\n\n- one\n- two\n"

QUOTED_ORIGINAL = "<html><body><div>From: sender@example.com<br>original body</div></body></html>"
UPLOAD_URL = "https://tenant.sharepoint.com/_api/upload/session-1"


@pytest.fixture()
def graph_config():
    return GraphConfig(
        max_retries=2,
        backoff_base_ms=0,
        timeout_seconds=5,
        max_pages=10,
        max_retry_after_seconds=300.0,
        error_message_max_length=200,
    )


@pytest.fixture()
def client(graph_config):
    with GraphClient(graph_config, lambda: TOKEN) as graph:
        yield graph


@pytest.fixture()
def upload():
    """The ceilings the previous implementation hardcoded, now config.

    `chunk_bytes` is the one that changed value. The old constant was 4 MiB
    with a comment claiming it was "a multiple of 320 KiB, as Graph requires";
    4 MiB is not (4194304 / 327680 = 12.8). Moving the number into config put a
    validator behind it and the validator found the discrepancy immediately --
    which is the argument for config over constants in one line.
    """
    return UploadConfig(
        inline_attachment_max_bytes=2_250_000,
        simple_upload_max_bytes=4 * 1024 * 1024,
        chunk_bytes=320 * 1024 * 12,
    )


@pytest.fixture()
def attachment_root(tmp_path) -> Path:
    """The committed assets plus the generated large one, in one writable root."""
    root = tmp_path / "assets"
    shutil.copytree(ASSETS, root)
    (root / "large.bin").write_bytes(LARGE_ATTACHMENT_BYTES)
    return root


@pytest.fixture()
def signature():
    return EmailSignatureConfig(
        html_path=None,
        logo_path="logo.png",
        logo_content_id=LOGO_CONTENT_ID,
    )


@pytest.fixture()
def outboxes_config(attachment_root, signature):
    return OutboxesConfig(
        attachment_root=str(attachment_root),
        forbidden_send_scopes=["Mail.Send"],
        definitions={
            "email.draft": OutboxDefinitionConfig(authority="draft_only", auth_profile="mail"),
            "email.reply": OutboxDefinitionConfig(authority="draft_only", auth_profile="mail"),
            "email.forward": OutboxDefinitionConfig(authority="draft_only", auth_profile="mail"),
            "teams.post_message": OutboxDefinitionConfig(authority="auto_send", auth_profile="teams"),
            "file.update": OutboxDefinitionConfig(authority="auto_send", auth_profile="files"),
        },
        email=EmailOutboxConfig(signature=signature),
        reconcile=ReconcileConfig(quote_markers=[r"^\s*From:\s"]),
    )


def build_intent(uuid: str, payload: dict, body: str) -> str:
    """Compose intent markdown the way an authoring agent would."""
    lines = [
        "---",
        f"uuid: {uuid}",
        "schema_version: 1",
        "created_at: 2026-08-05T09:00:00Z",
        "created_by: test",
        "payload:",
        *(f"  {line}" for line in json.dumps(payload, indent=2).splitlines()),
        "---",
        body,
    ]
    return "\n".join(lines)


def parse(uuid: str, payload: dict, body: str = BODY):
    """Build and parse an intent in one step, so the parser is always in the path."""
    return parse_intent(build_intent(uuid, payload, body), f"outbox/{uuid}", uuid)


def graph_recorder(recorded: list[dict]):
    """A respx side-effect that records requests and answers plausibly.

    Mirrors the responder the fixtures were recorded against, so any difference
    the parity test reports is a difference in the code under test rather than
    in the canned replies.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        recorded.append(request)
        path = request.url.path
        if request.method == "POST" and path.endswith("/createUploadSession"):
            return httpx.Response(200, json={"uploadUrl": UPLOAD_URL})
        if request.method == "PUT":
            return httpx.Response(201, json={"id": "uploaded"})
        if request.method == "POST" and path.endswith("/attachments"):
            return httpx.Response(201, json={"id": "att-1"})
        if request.method == "POST" and path.endswith(("createReply", "createReplyAll", "createForward")):
            return httpx.Response(201, json={"id": "NEW-1"})
        if request.method == "POST" and path.endswith("/messages"):
            return httpx.Response(201, json={"id": "MSG-1"})
        if request.method == "GET" and "MSG-DELETED" in path:
            # The deleted-draft branch: a liveness check that 404s.
            return httpx.Response(404, json={"error": {"code": "ErrorItemNotFound", "message": "gone"}})
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "id": "NEW-1",
                    "body": {"contentType": "html", "content": QUOTED_ORIGINAL},
                    "ccRecipients": [{"emailAddress": {"address": "existing@example.com"}}],
                },
            )
        if request.method == "PATCH":
            return httpx.Response(200, json={"id": "NEW-1"})
        raise AssertionError(f"unexpected request: {request.method} {request.url}")

    return handler


@pytest.fixture()
def recorded():
    """Records every request the code under test makes, in order."""
    requests: list[httpx.Request] = []
    with respx.mock:
        respx.route().mock(side_effect=graph_recorder(requests))
        yield requests
