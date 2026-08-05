"""Gate 2 -- one intent produces the request *sequence* the old sender produced.

The fixtures under `tests/fixtures/outbox/` were recorded in-process against a
respx mock, driving the implementation this package absorbed. No live send, no
real token: the recorder used a stub provider and refused to write any
`Authorization` header but the stub's.

**The assertion is on the whole sequence**, not on the final body, and that is
the point of the gate. The reply flow is `POST createReply` -> `GET the stub`
-> `PATCH the merge`; a port that collapsed it into a single POST would produce
a body-shaped result that a final-body assertion accepts and a recipient sees
with no quoted original. Only comparing method, URL, headers and body *in
order* catches that.

Known-and-accepted differences are normalised explicitly rather than by
loosening the assertion. There is exactly one, it happened at record time, and
`tests/fixtures/outbox/assets/README.md` records it: the signature logo's
content id moved from a module constant to config.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import httpx
import pytest

from m365_brain.m365.outboxes.email import EmailOutbox

from .conftest import BODY, LOGO_CONTENT_ID, SIGNATURE_HTML, parse

FIXTURES = Path(__file__).resolve().parents[3] / "fixtures" / "outbox"
MAX_INLINE_STRING = 1024

DRAFT = "email.draft"
REPLY = "email.reply"
FORWARD = "email.forward"

ORIGINAL = "AAMkAGE-ORIGINAL"

# One entry per recorded case: (fixture name, outbox name, payload).
# `include_signature` is the polarity flip of the recorded `skip_signature`.
CASES: dict[str, tuple[str, dict]] = {
    "new": (
        DRAFT,
        {
            "kind": DRAFT,
            "mailbox": "me",
            "to": ["a@example.com"],
            "cc": ["c@example.com"],
            "bcc": ["b@example.com"],
            "subject": "Quarterly note",
            "attachments": None,
            "inline_images": None,
            "include_signature": True,
            "revises_message_id": None,
        },
    ),
    "new_no_signature": (
        DRAFT,
        {
            "kind": DRAFT,
            "mailbox": "me",
            "to": ["a@example.com"],
            "cc": None,
            "bcc": None,
            "subject": "No signature",
            "attachments": None,
            "inline_images": None,
            "include_signature": False,
            "revises_message_id": None,
        },
    ),
    "new_attachment": (
        DRAFT,
        {
            "kind": DRAFT,
            "mailbox": "me",
            "to": ["a@example.com"],
            "cc": None,
            "bcc": None,
            "subject": "With a file",
            "attachments": [{"path": "doc.txt"}],
            "inline_images": None,
            "include_signature": True,
            "revises_message_id": None,
        },
    ),
    "new_inline_image": (
        DRAFT,
        {
            "kind": DRAFT,
            "mailbox": "me",
            "to": ["a@example.com"],
            "cc": None,
            "bcc": None,
            "subject": "With an image",
            "attachments": None,
            "inline_images": [{"kind_of_ref": "cid", "cid": "banner", "path": "banner.png"}],
            "include_signature": False,
            "revises_message_id": None,
        },
    ),
    "new_large_attachment": (
        DRAFT,
        {
            "kind": DRAFT,
            "mailbox": "me",
            "to": ["a@example.com"],
            "cc": None,
            "bcc": None,
            "subject": "Large file",
            "attachments": [{"path": "large.bin"}],
            "inline_images": None,
            "include_signature": False,
            "revises_message_id": None,
        },
    ),
    "shared_mailbox": (
        DRAFT,
        {
            "kind": DRAFT,
            "mailbox": "shared@example.com",
            "to": ["a@example.com"],
            "cc": None,
            "bcc": None,
            "subject": "From the shared mailbox",
            "attachments": None,
            "inline_images": None,
            "include_signature": True,
            "revises_message_id": None,
        },
    ),
    "reply": (
        REPLY,
        {
            "kind": REPLY,
            "mailbox": "me",
            "in_reply_to": ORIGINAL,
            "reply_all": False,
            "cc": None,
            "attachments": None,
            "inline_images": None,
            "include_signature": True,
            "revises_message_id": None,
        },
    ),
    "reply_all": (
        REPLY,
        {
            "kind": REPLY,
            "mailbox": "me",
            "in_reply_to": ORIGINAL,
            "reply_all": True,
            "cc": None,
            "attachments": None,
            "inline_images": None,
            "include_signature": True,
            "revises_message_id": None,
        },
    ),
    "reply_extra_cc": (
        REPLY,
        {
            "kind": REPLY,
            "mailbox": "me",
            "in_reply_to": ORIGINAL,
            "reply_all": False,
            "cc": ["extra@example.com", "EXISTING@example.com"],
            "attachments": None,
            "inline_images": None,
            "include_signature": True,
            "revises_message_id": None,
        },
    ),
    "forward": (
        FORWARD,
        {
            "kind": FORWARD,
            "mailbox": "me",
            "in_reply_to": ORIGINAL,
            "to": ["fwd@example.com", "second@example.com"],
            "cc": None,
            "attachments": None,
            "inline_images": None,
            "include_signature": True,
            "revises_message_id": None,
        },
    ),
    "update_in_place": (
        DRAFT,
        {
            "kind": DRAFT,
            "mailbox": "me",
            "to": ["a@example.com"],
            "cc": ["c@example.com"],
            "bcc": ["b@example.com"],
            "subject": "Revised subject",
            "attachments": None,
            "inline_images": None,
            "include_signature": True,
            "revises_message_id": "MSG-EXISTING",
        },
    ),
    "revise_recreates_when_gone": (
        DRAFT,
        {
            "kind": DRAFT,
            "mailbox": "me",
            "to": ["a@example.com"],
            "cc": ["c@example.com"],
            "bcc": ["b@example.com"],
            "subject": "Revised subject",
            "attachments": None,
            "inline_images": None,
            "include_signature": True,
            "revises_message_id": "MSG-DELETED",
        },
    ),
}

KEPT_HEADERS = ("Content-Type", "Content-Range", "If-Match")


def _digest(value: bytes) -> dict:
    return {"__sha256__": hashlib.sha256(value).hexdigest(), "__length__": len(value)}


def _shrink(value):
    if isinstance(value, str) and len(value) > MAX_INLINE_STRING:
        return _digest(value.encode())
    if isinstance(value, dict):
        return {k: _shrink(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_shrink(item) for item in value]
    return value


def _as_recorded(request: httpx.Request) -> dict:
    """Render a live request in the same shape the recorder wrote."""
    body = request.content
    if request.headers.get("Content-Type", "").startswith("application/json") or body[:1] in (b"{", b"["):
        parsed = _shrink(json.loads(body))
    elif body:
        parsed = _digest(body)
    else:
        parsed = None
    return {
        "method": request.method,
        "url": str(request.url),
        "headers": {k: request.headers[k] for k in KEPT_HEADERS if k in request.headers},
        "body": parsed,
    }


@pytest.fixture()
def outbox_for(client, upload, attachment_root, signature):
    def make(name: str) -> EmailOutbox:
        return EmailOutbox(
            name=name,
            client=client,
            upload=upload,
            attachment_root=str(attachment_root),
            signature=signature,
            signature_html=SIGNATURE_HTML,
        )

    return make


@pytest.mark.parametrize("case", sorted(CASES))
def test_request_sequence_matches_the_recording(case, outbox_for, recorded):
    outbox_name, payload = CASES[case]

    envelope = parse(f"uuid-{case}", payload, BODY)
    outbox_for(outbox_name).execute(envelope)

    actual = [_as_recorded(request) for request in recorded]
    expected = json.loads((FIXTURES / f"{case}.json").read_text(encoding="utf-8"))
    assert actual == expected


def test_no_fixture_is_unused():
    """A recorded case with no replay is a behaviour nobody is asserting."""
    recorded_cases = {path.stem for path in FIXTURES.glob("*.json")}
    assert recorded_cases == set(CASES)


def test_no_fixture_carries_a_real_token():
    """Belt and braces: the recorder refused to write one, and so does this."""
    for path in FIXTURES.glob("*.json"):
        text = path.read_text(encoding="utf-8")
        assert "Bearer" not in text
        assert "Authorization" not in text


def test_the_logo_content_id_is_the_configured_one_not_a_constant():
    """The one normalisation, pinned so it cannot quietly become two."""
    text = (FIXTURES / "new.json").read_text(encoding="utf-8")
    assert f'"contentId": "{LOGO_CONTENT_ID}"' in text
