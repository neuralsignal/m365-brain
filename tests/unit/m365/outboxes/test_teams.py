"""Channel posts: the payload shape and the delegated-only constraint."""

from __future__ import annotations

import json

import pytest

from m365_brain.m365.outboxes.teams import TeamsIntentError, TeamsPostOutbox
from m365_brain.vault.dispatch import DRAFT_ONLY_OPS, GraphOp

from .conftest import parse

POST = {"kind": "teams.post_message", "team_id": "T1", "channel_id": "C1"}
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


@pytest.fixture()
def outbox(client):
    return TeamsPostOutbox(name="teams.post_message", client=client)


def test_it_posts_the_html_envelope_graph_expects(outbox, recorded):
    result = outbox.execute(parse("u1", POST, "<p>hello channel</p>"))

    assert result.graph_message_id == "MSG-1"
    assert recorded[0].url.path.endswith("/teams/T1/channels/C1/messages")
    assert json.loads(recorded[0].content) == {"body": {"contentType": "html", "content": "<p>hello channel</p>"}}


def test_the_body_is_sent_as_authored(outbox, recorded):
    """The payload carries HTML rather than markdown because Graph accepts only
    a limited subset; rendering here would emit tags it silently drops."""
    outbox.execute(parse("u1", POST, "<p>a</p><ul><li>b</li></ul>"))

    assert json.loads(recorded[0].content)["body"]["content"] == "<p>a</p><ul><li>b</li></ul>"


def test_a_payload_of_the_wrong_kind_is_refused(outbox, recorded):
    with pytest.raises(TeamsIntentError):
        outbox.execute(parse("u1", DRAFT))

    assert recorded == []


def test_it_declares_only_the_channel_post_operation(outbox):
    """`ChannelMessage.Send` has no application variant, so this is delegated
    by construction -- and it is emphatically not a drafting operation."""
    assert outbox.declared_ops == frozenset({GraphOp.POST_CHANNEL})
    assert not outbox.declared_ops & DRAFT_ONLY_OPS
