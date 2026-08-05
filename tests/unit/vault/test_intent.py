"""Parsing an intent file, and the two identity rules that go with it.

Both failure cases here were real defects in the implementation this ports:
a frontmatter `body:` key was silently overwritten by the markdown body, and
the filename stem was never checked against the envelope uuid -- so three code
paths could disagree about which item they were archiving.
"""

from __future__ import annotations

import json

import pytest
from hypothesis import given
from hypothesis import strategies as st

from m365_brain.vault.intent import IntentEnvelope, IntentParseError, dump_intent, parse_intent, parse_intent_file

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


def compose(uuid: str, payload: dict, content: str, **envelope) -> str:
    fields = {
        "uuid": uuid,
        "schema_version": 1,
        "created_at": "2026-08-05T09:00:00Z",
        "created_by": "test",
        **envelope,
    }
    lines = ["---"]
    lines += [f"{key}: {value}" for key, value in fields.items()]
    lines.append("payload:")
    lines += [f"  {line}" for line in json.dumps(payload, indent=2).splitlines()]
    lines += ["---", content]
    return "\n".join(lines)


class TestParsing:
    def test_the_markdown_body_becomes_the_payload_body(self):
        envelope = parse_intent(compose("abc", DRAFT, "Dear Ada,\n\nthanks."), "ref", "abc")

        assert envelope.payload.body.strip() == "Dear Ada,\n\nthanks."

    def test_the_envelope_carries_who_and_when(self):
        envelope = parse_intent(compose("abc", DRAFT, "body"), "ref", "abc")

        assert envelope.uuid == "abc"
        assert envelope.created_by == "test"
        assert envelope.schema_version == 1
        assert envelope.created_at.year == 2026

    def test_kind_is_derived_from_the_payload_not_duplicated(self):
        """There is no `outbox:` field. Two values that must agree is the
        defect DRY names, and the source needed a gate purely to police it."""
        envelope = parse_intent(compose("abc", DRAFT, "body"), "ref", "abc")

        assert envelope.kind == "email.draft"
        assert "outbox" not in IntentEnvelope.model_fields
        assert "integration" not in IntentEnvelope.model_fields


class TestRejections:
    def test_a_frontmatter_body_key_is_a_hard_error(self):
        content = compose("abc", DRAFT, "markdown body", body="a frontmatter body")

        with pytest.raises(IntentParseError) as excinfo:
            parse_intent(content, "ref", "abc")

        assert "silently overwritten" in str(excinfo.value)

    def test_a_body_inside_the_payload_is_a_hard_error(self):
        content = compose("abc", {**DRAFT, "body": "sneaky"}, "markdown body")

        with pytest.raises(IntentParseError) as excinfo:
            parse_intent(content, "ref", "abc")

        assert "markdown body" in str(excinfo.value)

    def test_a_stem_that_disagrees_with_the_uuid_is_unresolvable(self):
        with pytest.raises(IntentParseError) as excinfo:
            parse_intent(compose("written", DRAFT, "body"), "ref", "filename")

        assert "does not match the filename stem" in str(excinfo.value)

    def test_a_missing_payload_names_the_key(self):
        with pytest.raises(IntentParseError) as excinfo:
            parse_intent("---\nuuid: abc\n---\nbody", "ref", "abc")

        assert "payload" in str(excinfo.value)

    def test_an_unknown_envelope_key_is_rejected(self):
        content = compose("abc", DRAFT, "body", priority="high")

        with pytest.raises(IntentParseError):
            parse_intent(content, "ref", "abc")

    def test_the_source_ref_is_always_in_the_message(self):
        with pytest.raises(IntentParseError) as excinfo:
            parse_intent("---\nuuid: abc\n---\nbody", "pending/email.draft/abc.md", "abc")

        assert "pending/email.draft/abc.md" in str(excinfo.value)

    def test_an_invalid_payload_surfaces_the_field_error(self):
        with pytest.raises(IntentParseError) as excinfo:
            parse_intent(compose("abc", {**DRAFT, "to": []}, "body"), "ref", "abc")

        assert "to" in str(excinfo.value)


class TestFiles:
    def test_the_stem_is_the_expected_uuid(self, tmp_path):
        path = tmp_path / "abc.md"
        path.write_text(compose("abc", DRAFT, "body"), encoding="utf-8")

        assert parse_intent_file(path).uuid == "abc"

    def test_an_unreadable_file_names_itself(self, tmp_path):
        with pytest.raises(IntentParseError) as excinfo:
            parse_intent_file(tmp_path / "gone.md")

        assert "gone.md" in str(excinfo.value)


class TestRoundTrip:
    def test_dump_then_parse_reproduces_the_envelope(self):
        original = parse_intent(compose("abc", DRAFT, "Body text."), "ref", "abc")

        reparsed = parse_intent(dump_intent(original), "ref", "abc")

        assert reparsed == original

    def test_the_dumped_form_keeps_the_body_out_of_the_frontmatter(self):
        original = parse_intent(compose("abc", DRAFT, "Body text."), "ref", "abc")

        dumped = dump_intent(original)

        frontmatter_block = dumped.split("---")[1]
        assert "Body text." not in frontmatter_block
        assert "Body text." in dumped

    @given(body=st.text(min_size=1, max_size=300).filter(lambda s: s.strip() and "---" not in s))
    def test_any_body_survives_a_round_trip(self, body):
        original = parse_intent(compose("abc", DRAFT, body), "ref", "abc")

        assert parse_intent(dump_intent(original), "ref", "abc").payload.body == original.payload.body
