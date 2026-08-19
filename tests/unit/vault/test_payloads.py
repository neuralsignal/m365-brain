"""Payload validation: the strictness is the feature.

The schema this ports had `payload: dict[str, Any]` and fetched a validator it
then threw away, so none of its `extra="forbid"` / `strict=True` settings ever
fired. Every test here is an assertion that one of them now does.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import TypeAdapter, ValidationError

from m365_brain.vault.payloads import (
    PAYLOAD_KINDS,
    Attachment,
    EmailDraftPayload,
    EmailForwardPayload,
    EmailReplyPayload,
    FileUpdatePayload,
    InlineImage,
    IntentPayload,
    TeamsPostPayload,
)

ADAPTER = TypeAdapter(IntentPayload)

DRAFT = {
    "kind": "email.draft",
    "mailbox": "me",
    "to": ["a@example.com"],
    "cc": None,
    "bcc": None,
    "subject": "Hello",
    "body": "text",
    "attachments": None,
    "inline_images": None,
    "include_signature": True,
    "revises_message_id": None,
}

REPLY = {
    "kind": "email.reply",
    "mailbox": "me",
    "in_reply_to": "AAMkAGE1",
    "reply_all": False,
    "cc": None,
    "body": "text",
    "attachments": None,
    "inline_images": None,
    "include_signature": True,
    "revises_message_id": None,
}

FORWARD = {**REPLY, "kind": "email.forward", "to": ["b@example.com"]}
del FORWARD["reply_all"]

TEAMS = {"kind": "teams.post_message", "team_id": "T1", "channel_id": "C1", "body": "<p>hi</p>"}

FILE = {
    "kind": "file.update",
    "site_hostname": "contoso.example.com",
    "site_path": "sites/Team",
    "library_name": "Documents",
    "item_path": "reports/q3.md",
    "etag": None,
    "content_type": "text/markdown",
    "body": "# Q3",
}

ALL_PAYLOADS = [DRAFT, REPLY, FORWARD, TEAMS, FILE]


class TestDiscrimination:
    @pytest.mark.parametrize("payload", ALL_PAYLOADS)
    def test_every_kind_round_trips(self, payload):
        parsed = ADAPTER.validate_python(payload)
        assert parsed.kind == payload["kind"]
        assert parsed.model_dump(mode="json") == payload

    def test_the_declared_kinds_match_the_union(self):
        """A kind in the tuple with no model, or the reverse, would make a
        valid intent unroutable."""
        modelled = {EmailDraftPayload, EmailReplyPayload, EmailForwardPayload, TeamsPostPayload, FileUpdatePayload}
        assert len(modelled) == len(PAYLOAD_KINDS)
        for payload in ALL_PAYLOADS:
            assert payload["kind"] in PAYLOAD_KINDS

    def test_an_unknown_kind_is_rejected(self):
        with pytest.raises(ValidationError):
            ADAPTER.validate_python({**DRAFT, "kind": "email.send"})


class TestForbiddenExtras:
    @pytest.mark.parametrize("payload", ALL_PAYLOADS)
    def test_an_unknown_key_is_a_rejection_not_a_warning(self, payload):
        with pytest.raises(ValidationError) as excinfo:
            ADAPTER.validate_python({**payload, "priority": "high"})

        assert "priority" in str(excinfo.value)


class TestNoDefaults:
    @pytest.mark.parametrize("field", ["cc", "bcc", "attachments", "inline_images", "include_signature"])
    def test_an_omitted_optional_field_fails_rather_than_defaulting(self, field):
        """`X | None` is a required key that must be spelled `null`. An author
        who forgot `cc:` gets an error naming it, not a silent empty list."""
        payload = {key: value for key, value in DRAFT.items() if key != field}

        with pytest.raises(ValidationError) as excinfo:
            ADAPTER.validate_python(payload)

        assert field in str(excinfo.value)

    def test_null_is_accepted_where_none_is_meaningful(self):
        assert ADAPTER.validate_python({**DRAFT, "cc": None}).cc is None


class TestStrictness:
    def test_a_stringly_typed_boolean_is_rejected(self):
        with pytest.raises(ValidationError):
            ADAPTER.validate_python({**REPLY, "reply_all": "true"})

    def test_a_stringly_typed_recipient_list_is_rejected(self):
        """The implementation this replaces auto-split `a@x.com; b@y.com` with
        a logged warning, which is exactly the coercion that lets a malformed
        address reach Graph."""
        with pytest.raises(ValidationError):
            ADAPTER.validate_python({**DRAFT, "to": "a@example.com; b@example.com"})

    def test_a_malformed_address_is_rejected(self):
        with pytest.raises(ValidationError):
            ADAPTER.validate_python({**DRAFT, "to": ["not-an-address"]})

    def test_an_empty_recipient_list_is_rejected(self):
        with pytest.raises(ValidationError):
            ADAPTER.validate_python({**DRAFT, "to": []})

    def test_an_empty_mailbox_is_rejected(self):
        with pytest.raises(ValidationError):
            ADAPTER.validate_python({**DRAFT, "mailbox": ""})

    def test_a_subject_beyond_the_header_ceiling_is_rejected(self):
        with pytest.raises(ValidationError):
            ADAPTER.validate_python({**DRAFT, "subject": "x" * 999})


class TestLegacyMessageIds:
    @pytest.mark.parametrize("kind_payload", [REPLY, FORWARD])
    def test_a_mapi_entry_id_is_refused_with_an_actionable_message(self, kind_payload):
        """The pre-Graph extractors emitted these; passing one to Graph
        produces an opaque 400, so it is caught at parse instead."""
        legacy = "00000000" + "AB12CD34EF567890" * 2

        with pytest.raises(ValidationError) as excinfo:
            ADAPTER.validate_python({**kind_payload, "in_reply_to": legacy})

        assert "Re-ingest" in str(excinfo.value)

    def test_a_graph_id_passes(self):
        assert ADAPTER.validate_python({**REPLY, "in_reply_to": "AAMkAGE1234"}).in_reply_to == "AAMkAGE1234"


class TestNestedModels:
    def test_an_inline_image_names_its_reference_scheme(self):
        image = InlineImage(kind_of_ref="cid", cid="banner", path="banner.png")
        assert image.kind_of_ref == "cid"

    def test_an_unknown_reference_scheme_is_rejected(self):
        with pytest.raises(ValidationError):
            InlineImage(kind_of_ref="url", cid="banner", path="banner.png")

    def test_an_attachment_is_a_path_not_inline_bytes(self):
        assert set(Attachment.model_fields) == {"path"}

    def test_an_absolute_attachment_path_is_rejected(self):
        with pytest.raises(ValidationError, match="absolute"):
            Attachment(path="/etc/passwd")

    def test_a_dotdot_attachment_path_is_rejected(self):
        with pytest.raises(ValidationError, match="\\.\\."):
            Attachment(path="../../secret.txt")

    def test_an_absolute_inline_image_path_is_rejected(self):
        with pytest.raises(ValidationError, match="absolute"):
            InlineImage(kind_of_ref="cid", cid="banner", path="/etc/shadow")

    def test_a_dotdot_inline_image_path_is_rejected(self):
        with pytest.raises(ValidationError, match="\\.\\."):
            InlineImage(kind_of_ref="cid", cid="banner", path="../../../etc/passwd")

    def test_a_relative_attachment_path_is_accepted(self):
        assert Attachment(path="sub/file.pdf").path == "sub/file.pdf"

    def test_a_relative_inline_image_path_is_accepted(self):
        img = InlineImage(kind_of_ref="cid", cid="logo", path="images/logo.png")
        assert img.path == "images/logo.png"

    def test_payloads_are_frozen(self):
        payload = ADAPTER.validate_python(DRAFT)
        with pytest.raises(ValidationError):
            payload.subject = "changed"


class TestFileUpdateRouting:
    def test_a_null_etag_means_create_only(self):
        assert ADAPTER.validate_python(FILE).etag is None

    def test_a_string_etag_means_update_only(self):
        assert ADAPTER.validate_python({**FILE, "etag": '"e1"'}).etag == '"e1"'

    def test_there_is_no_third_option(self):
        """No `overwrite: true`, no `force`. The two-way branch on a nullable
        is the whole routing, so an intent cannot ask for an unconditional
        write."""
        assert set(FileUpdatePayload.model_fields) == set(FILE)


@given(
    subject=st.text(min_size=1, max_size=200).filter(lambda s: s.strip()),
    body=st.text(max_size=500),
    include_signature=st.booleans(),
)
def test_a_draft_payload_round_trips_for_any_text(subject, body, include_signature):
    payload = {**DRAFT, "subject": subject, "body": body, "include_signature": include_signature}
    assert ADAPTER.validate_python(payload).model_dump(mode="json") == payload
