"""The Graph draft operations, in the request sequence they have to keep.

Ported call-for-call from a sender that has been creating real drafts for
months. Two shapes here are load-bearing and are the reason this is a port
rather than a rewrite:

**Mailbox dispatch.** `me` routes to `/me`, anything else is a UPN and routes
to `/users/{upn}`. A shared-mailbox draft has to land in the *shared* Drafts
folder; sending it to `/me` produces a draft nobody reviewing that mailbox will
ever see.

**The three-step reply.** `POST createReply` -> `GET the new draft` ->
`PATCH the merged body`. The middle call exists solely to read back the quoted
original Graph generated, and skipping it -- posting a body straight to
`createReply` -- silently drops the quote. A test that asserted only on the
final body would pass either way, which is exactly why the parity gate asserts
on the whole sequence.
"""

from __future__ import annotations

from typing import Any

import structlog

from m365_brain.m365.client import GraphClient
from m365_brain.m365.errors import GraphNotFoundError
from m365_brain.m365.outboxes.rendering import compose_with_signature, merge_reply_body

log = structlog.get_logger()

PERSONAL_MAILBOX = "me"

REPLY = "createReply"
REPLY_ALL = "createReplyAll"
FORWARD = "createForward"


def mailbox_base(mailbox: str) -> str:
    """`/me` for the personal mailbox, `/users/{upn}` for any other."""
    if not mailbox or mailbox == PERSONAL_MAILBOX:
        return f"/{PERSONAL_MAILBOX}"
    return f"/users/{mailbox}"


def recipient_list(addresses: list[str]) -> list[dict[str, dict[str, str]]]:
    """Graph's recipient envelope."""
    return [{"emailAddress": {"address": address}} for address in addresses]


def merge_cc(existing: list[dict[str, dict[str, str]]] | None, extra: list[str]) -> list[dict[str, dict[str, str]]]:
    """Fold extra cc addresses into the list Graph derived, case-insensitively.

    Graph populates `ccRecipients` on a `createReplyAll` stub. Replacing that
    list instead of merging it drops everyone the reply-all was for.
    """
    merged = list(existing or [])
    seen = {entry["emailAddress"]["address"].casefold() for entry in merged}
    for address in extra:
        if address.casefold() not in seen:
            merged.append({"emailAddress": {"address": address}})
            seen.add(address.casefold())
    return merged


def create_new_draft(
    client: GraphClient,
    mailbox: str,
    to: list[str],
    cc: list[str],
    bcc: list[str],
    subject: str,
    body_html: str,
    signature_html: str,
) -> str:
    """POST a new draft. Returns the Graph message id."""
    response = client.post(
        f"{mailbox_base(mailbox)}/messages",
        _message_payload(subject, body_html, signature_html, to, cc, bcc),
    )
    message_id = str(response.json()["id"])
    log.info("outbox.email.draft_created", mailbox=mailbox, message_id=message_id[:20])
    return message_id


def update_draft(
    client: GraphClient,
    mailbox: str,
    message_id: str,
    to: list[str],
    cc: list[str],
    bcc: list[str],
    subject: str,
    body_html: str,
    signature_html: str,
) -> str:
    """PATCH an existing unsent draft in place. Returns `message_id`.

    Inline images attached when the draft was created persist across a body
    PATCH, so they are deliberately not re-attached here. Re-attaching would
    duplicate every one of them on each revision.
    """
    client.patch(
        f"{mailbox_base(mailbox)}/messages/{message_id}",
        _message_payload(subject, body_html, signature_html, to, cc, bcc),
    )
    log.info("outbox.email.draft_updated", mailbox=mailbox, message_id=message_id[:20])
    return message_id


def create_reply_like(
    client: GraphClient,
    mailbox: str,
    original_message_id: str,
    action: str,
    body_html: str,
    signature_html: str,
    extra_cc: list[str],
    forward_to: list[str] | None,
) -> str:
    """The three-step reply/reply-all/forward pattern. Returns the new id."""
    base = mailbox_base(mailbox)
    created = client.post(f"{base}/messages/{original_message_id}/{action}", None)
    new_id = str(created.json()["id"])

    stub = client.get(f"{base}/messages/{new_id}", None)
    quoted = stub.get("body", {}).get("content", "")
    merged_cc = merge_cc(stub.get("ccRecipients") or [], extra_cc)

    payload: dict[str, Any] = {
        "body": {"contentType": "html", "content": merge_reply_body(quoted, body_html, signature_html)},
    }
    if merged_cc:
        payload["ccRecipients"] = merged_cc
    if forward_to is not None:
        payload["toRecipients"] = recipient_list(forward_to)
    client.patch(f"{base}/messages/{new_id}", payload)

    log.info(
        "outbox.email.reply_like_created",
        action=action,
        mailbox=mailbox,
        original_message_id=original_message_id[:20],
        message_id=new_id[:20],
    )
    return new_id


def get_message(client: GraphClient, mailbox: str, message_id: str, select: list[str]) -> dict | None:
    """Fetch one message, or None when it is gone.

    A deleted draft is *data*, not an error -- it is how a rejection is
    detected -- so 404 returns None and every other failure propagates.

    The `$select` list is spelled into the path rather than passed as params so
    the recorded request matches the one this replaces character for character.
    """
    path = f"{mailbox_base(mailbox)}/messages/{message_id}"
    if select:
        path = f"{path}?$select={','.join(select)}"
    try:
        return client.get(path, None)
    except GraphNotFoundError:
        log.info("outbox.email.message_not_found", mailbox=mailbox, message_id=message_id[:20])
        return None


def _message_payload(
    subject: str,
    body_html: str,
    signature_html: str,
    to: list[str],
    cc: list[str],
    bcc: list[str],
) -> dict[str, Any]:
    """One payload builder, so create and update cannot drift apart."""
    return {
        "subject": subject,
        "body": {"contentType": "html", "content": compose_with_signature(body_html, signature_html)},
        "toRecipients": recipient_list(to),
        "ccRecipients": recipient_list(cc),
        "bccRecipients": recipient_list(bcc),
    }
