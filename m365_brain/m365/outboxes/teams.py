"""Posting a message to a Teams channel.

Delegated-only by construction: `ChannelMessage.Send` has no application
variant, so this workload always runs as the signed-in user. There is no
app-only fallback to add later and no config flag to expose for one.

Graph accepts only a limited HTML subset in a channel message -- paragraphs,
bold, italics, links, lists. The payload carries HTML rather than markdown for
that reason: rendering markdown here would produce tags Graph silently drops,
and the poster would see a message that looks nothing like the source.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog

from m365_brain.m365.client import GraphClient
from m365_brain.vault.dispatch import DispatchResult, GraphOp
from m365_brain.vault.intent import IntentEnvelope

log = structlog.get_logger()

TEAMS_POST_KIND = "teams.post_message"
HTML_CONTENT_TYPE = "html"


class TeamsIntentError(Exception):
    """The intent is not a channel post."""


@dataclass(frozen=True)
class TeamsPostOutbox:
    """Posts one HTML message to one channel."""

    name: str
    client: GraphClient

    declared_ops: frozenset[GraphOp] = frozenset({GraphOp.POST_CHANNEL})

    def execute(self, envelope: IntentEnvelope) -> DispatchResult:
        payload = envelope.payload
        if payload.kind != TEAMS_POST_KIND:
            raise TeamsIntentError(f"outbox {self.name!r} received a {payload.kind!r} payload")
        response = self.client.post(
            f"/teams/{payload.team_id}/channels/{payload.channel_id}/messages",
            {"body": {"contentType": HTML_CONTENT_TYPE, "content": payload.body}},
        )
        message_id = str(response.json()["id"])
        log.info(
            "outbox.teams.message_posted",
            team_id=payload.team_id,
            channel_id=payload.channel_id,
            message_id=message_id,
        )
        return DispatchResult(graph_message_id=message_id)
