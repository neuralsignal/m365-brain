"""The email handler: signature suppression, ordering, and the revision paths.

The parity test pins the exact request sequences against recordings. These
cover the branches the recordings cannot: what happens when an asset is
missing, and what a revision does that the payload has no authority over.
"""

from __future__ import annotations

import json

import pytest

from m365_brain.config import EmailSignatureConfig
from m365_brain.m365.outboxes.email import EmailIntentError, EmailOutbox, load_signature_html
from m365_brain.vault.dispatch import DRAFT_ONLY_OPS, GraphOp

from .conftest import LOGO_CONTENT_ID, SIGNATURE_HTML, parse

DRAFT = {
    "kind": "email.draft",
    "mailbox": "me",
    "to": ["a@example.com"],
    "cc": None,
    "bcc": None,
    "subject": "Hello",
    "attachments": None,
    "inline_images": None,
    "include_signature": True,
    "revises_message_id": None,
}

REPLY = {
    "kind": "email.reply",
    "mailbox": "me",
    "in_reply_to": "AAMkAGE-ORIG",
    "reply_all": False,
    "cc": None,
    "attachments": None,
    "inline_images": None,
    "include_signature": True,
    "revises_message_id": None,
}


@pytest.fixture()
def outbox(client, upload, attachment_root, signature):
    def make(name: str = "email.draft") -> EmailOutbox:
        return EmailOutbox(
            name=name,
            client=client,
            upload=upload,
            attachment_root=str(attachment_root),
            signature=signature,
            signature_html=SIGNATURE_HTML,
        )

    return make


def _attachment_bodies(recorded):
    return [json.loads(r.content) for r in recorded if r.url.path.endswith("/attachments")]


class TestDeclaredOps:
    def test_the_handler_declares_only_drafting_operations(self, outbox):
        assert outbox().declared_ops == DRAFT_ONLY_OPS
        assert GraphOp.SEND_MAIL not in outbox().declared_ops


class TestSignature:
    def test_the_signature_and_its_logo_travel_together(self, outbox, recorded):
        outbox().execute(parse("u1", DRAFT))

        logos = [body for body in _attachment_bodies(recorded) if body.get("contentId") == LOGO_CONTENT_ID]
        assert len(logos) == 1
        assert logos[0]["isInline"] is True

    def test_suppressing_the_signature_suppresses_its_logo(self, outbox, recorded):
        """There is no `cid:` reference left for the logo to resolve, and the
        configured logo path may not even be valid on this deployment."""
        outbox().execute(parse("u1", {**DRAFT, "include_signature": False}))

        assert _attachment_bodies(recorded) == []
        assert "Test Sender" not in json.loads(recorded[0].content)["body"]["content"]

    def test_a_configuration_with_no_logo_still_signs(self, client, upload, attachment_root, recorded):
        handler = EmailOutbox(
            name="email.draft",
            client=client,
            upload=upload,
            attachment_root=str(attachment_root),
            signature=EmailSignatureConfig(html_path=None, logo_path=None, logo_content_id="unused"),
            signature_html=SIGNATURE_HTML,
        )

        handler.execute(parse("u1", DRAFT))

        assert "Test Sender" in json.loads(recorded[0].content)["body"]["content"]
        assert _attachment_bodies(recorded) == []

    def test_loading_a_missing_signature_file_is_a_startup_crash(self, tmp_path):
        config = EmailSignatureConfig(html_path=str(tmp_path / "nope.html"), logo_path=None, logo_content_id="x")

        with pytest.raises(FileNotFoundError) as excinfo:
            load_signature_html(config)

        assert "null" in str(excinfo.value)

    def test_a_null_signature_path_means_no_signature(self):
        config = EmailSignatureConfig(html_path=None, logo_path=None, logo_content_id="x")

        assert load_signature_html(config) == ""

    def test_a_configured_signature_is_read_from_disk(self, tmp_path):
        path = tmp_path / "sig.html"
        path.write_text("<p>from disk</p>", encoding="utf-8")
        config = EmailSignatureConfig(html_path=str(path), logo_path=None, logo_content_id="x")

        assert load_signature_html(config) == "<p>from disk</p>"


class TestFailFast:
    def test_a_missing_attachment_raises_before_any_graph_call(self, outbox, recorded):
        """A draft that exists with only some of its files is worse than no
        draft plus the path that was missing."""
        payload = {**DRAFT, "attachments": [{"path": "deck.pdf"}]}

        with pytest.raises(FileNotFoundError):
            outbox().execute(parse("u1", payload))

        assert recorded == []

    def test_a_missing_inline_image_raises_before_any_graph_call(self, outbox, recorded):
        payload = {
            **DRAFT,
            "inline_images": [{"kind_of_ref": "cid", "cid": "poster", "path": "poster.png"}],
        }

        with pytest.raises(FileNotFoundError):
            outbox().execute(parse("u1", payload))

        assert recorded == []

    def test_a_payload_of_the_wrong_kind_is_refused(self, outbox, recorded):
        with pytest.raises(EmailIntentError) as excinfo:
            outbox("email.draft").execute(parse("u1", REPLY))

        assert "email.reply" in str(excinfo.value)
        assert recorded == []


class TestAttachmentOrder:
    def test_user_files_then_logo_then_inline_images(self, outbox, recorded):
        payload = {
            **DRAFT,
            "attachments": [{"path": "doc.txt"}],
            "inline_images": [{"kind_of_ref": "cid", "cid": "banner", "path": "banner.png"}],
        }

        outbox().execute(parse("u1", payload))

        names = [(body["name"], body.get("contentId")) for body in _attachment_bodies(recorded)]
        assert names == [("doc.txt", None), ("logo.png", LOGO_CONTENT_ID), ("banner.png", "banner")]


class TestRevision:
    def test_a_live_draft_is_patched_in_place_and_keeps_its_id(self, outbox, recorded):
        payload = {**DRAFT, "revises_message_id": "MSG-EXISTING"}

        result = outbox().execute(parse("u1", payload))

        assert result.graph_message_id == "MSG-EXISTING"
        assert [request.method for request in recorded] == ["GET", "PATCH"]

    def test_inline_images_are_not_re_attached_on_a_patch(self, outbox, recorded):
        """They persist across a body PATCH; re-attaching duplicates every one
        of them on each revision."""
        payload = {
            **DRAFT,
            "revises_message_id": "MSG-EXISTING",
            "inline_images": [{"kind_of_ref": "cid", "cid": "banner", "path": "banner.png"}],
        }

        outbox().execute(parse("u1", payload))

        assert _attachment_bodies(recorded) == []

    def test_a_deleted_target_becomes_a_fresh_draft(self, outbox, recorded):
        payload = {**DRAFT, "revises_message_id": "MSG-DELETED"}

        result = outbox().execute(parse("u1", payload))

        assert result.graph_message_id == "MSG-1"
        assert recorded[1].method == "POST"

    def test_revising_a_reply_is_refused_rather_than_approximated(self, outbox, recorded):
        """The stored body is `user text + signature + Graph's quote` and the
        three are indistinguishable once merged. The implementation this
        replaces patched anyway and dropped the quote, silently."""
        payload = {**REPLY, "revises_message_id": "MSG-EXISTING"}

        with pytest.raises(EmailIntentError) as excinfo:
            outbox("email.reply").execute(parse("u1", payload))

        assert "quoted original" in str(excinfo.value)
        assert recorded == []


class TestPointOfNoReturn:
    """A failure *after* the draft exists must not read as retryable.

    `outbox/runner.py` puts an intent back in its outbox when the exception
    carries a truthy `transient`, on the stated ground that a raising handler
    reached no message id and therefore sent nothing. That holds for a
    single-request handler and not for this one: `_create` is a draft POST
    followed by one POST per attachment, so between them the draft is already
    in the mailbox and a release would create a second one on the next pass --
    the one failure mode worse than a dropped draft.
    """

    class Transient(Exception):
        transient = True

    def _raise_transient(self, *_args, **_kwargs):
        raise self.Transient("identity provider unreachable")

    def test_a_transient_failure_after_the_draft_exists_is_terminal(self, outbox, recorded, monkeypatch):
        monkeypatch.setattr("m365_brain.m365.outboxes.email.attach_file", self._raise_transient)
        payload = {**DRAFT, "attachments": [{"path": "doc.txt"}]}

        with pytest.raises(self.Transient) as excinfo:
            outbox().execute(parse("u1", payload))

        assert excinfo.value.transient is False, "releasing here would draft the mail twice"
        assert [r.url.path for r in recorded] == ["/v1.0/me/messages"], "the draft POST did land"

    def test_the_exception_type_survives_so_classification_still_works(self, outbox, recorded, monkeypatch):
        """`_classify_failure` reads the type and `status_code` off this same
        exception, so the downgrade sets an attribute rather than wrapping."""

        def _vanished(*_args, **_kwargs):
            raise FileNotFoundError("doc.txt went away after it was resolved")

        monkeypatch.setattr("m365_brain.m365.outboxes.email.attach_file", _vanished)
        payload = {**DRAFT, "attachments": [{"path": "doc.txt"}]}

        with pytest.raises(FileNotFoundError):
            outbox().execute(parse("u1", payload))
