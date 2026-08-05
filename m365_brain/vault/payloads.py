"""What an intent asks for -- one model per outbox, in one discriminated union.

These live under `vault` rather than under `outbox` because an intent is a
*file*: `<outbox>/<name>/<uuid>.md` is a vault artifact exactly like an inbox
entry is, and `vault` is the layer both the lifecycle (`outbox`) and the
executor (`m365`) may read. Putting them in either peer would force the other
to import it, and the two are peers by construction.

Three shapes here are corrections to the schema this was ported from, not
preferences:

* the payload is a **discriminated union**, not `dict[str, Any]`. The original
  fetched a payload schema and then discarded it, so `extra="forbid"` fired
  nowhere and no production intent was ever validated. A union makes
  validation structural -- there is no way to parse an envelope without it.
* `X | None` fields carry **no default**. An author who omits `cc:` gets an
  error naming the key, not a silent empty list.
* an attachment is a **path**, not inline base64. A base64 blob in YAML
  frontmatter is unreadable and undiffable, and Graph's bulk cap means the
  model could not express what the executor needs anyway.
"""

from __future__ import annotations

import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

PAYLOAD_MODEL_CONFIG = ConfigDict(extra="forbid", frozen=True, strict=True)

# Pre-Graph extractors emitted MAPI EntryIDs: 8+ leading zeros then long
# uppercase hex. Graph REST ids are base64-ish and start with letters. Passing
# one to Graph produces an opaque 400, so it is rejected here, at parse time,
# where the message can say what to do about it.
LEGACY_MAPI_ENTRY_ID = re.compile(r"^0{8,}[0-9A-F]{16,}$")

MAX_SUBJECT_LENGTH = 998  # RFC 5322 line-length ceiling for a header


def _reject_legacy_entry_id(value: str) -> str:
    if LEGACY_MAPI_ENTRY_ID.match(value):
        raise ValueError(
            f"in_reply_to {value[:30]}... is a legacy MAPI EntryID, not a Graph message id. "
            "Re-ingest the source message so its frontmatter carries a Graph id."
        )
    return value


class InlineImage(BaseModel):
    """An image the body references as `<img src="cid:...">`."""

    model_config = PAYLOAD_MODEL_CONFIG
    kind_of_ref: Literal["cid"]
    """Only `cid` references exist today. A Literal keeps the door named rather
    than leaving a bare string that a second scheme could quietly occupy."""

    cid: str = Field(min_length=1, max_length=128)
    path: str = Field(min_length=1)


class Attachment(BaseModel):
    """A file to attach, resolved against `outboxes.attachment_root` at dispatch."""

    model_config = PAYLOAD_MODEL_CONFIG
    path: str = Field(min_length=1)


class _EmailCommon(BaseModel):
    """Fields every email payload carries. Shared so the three cannot drift."""

    model_config = PAYLOAD_MODEL_CONFIG
    mailbox: str = Field(min_length=1)
    """`me`, or a UPN. No inference and no default: a shared-mailbox draft that
    silently lands in a personal Drafts folder is invisible until someone looks
    for it in the wrong place."""

    body: str
    """Markdown. Supplied by the file's markdown body, never by a frontmatter key."""

    attachments: list[Attachment] | None
    inline_images: list[InlineImage] | None
    include_signature: bool
    """The polarity flip of the ported `skip_signature`. Stated on every intent
    because a silently inverted boolean is the most plausible port bug here."""

    revises_message_id: str | None
    """None creates a draft; a message id PATCHes that draft in place."""


class EmailDraftPayload(_EmailCommon):
    kind: Literal["email.draft"]
    to: list[EmailStr] = Field(min_length=1)
    cc: list[EmailStr] | None
    bcc: list[EmailStr] | None
    subject: str = Field(min_length=1, max_length=MAX_SUBJECT_LENGTH)


class EmailReplyPayload(_EmailCommon):
    kind: Literal["email.reply"]
    in_reply_to: str = Field(min_length=1, max_length=512)
    reply_all: bool
    """A field, not a fourth outbox: the ported `action` enum
    (`new|reply|reply_all|forward`) is exactly `kind` x `reply_all`."""

    cc: list[EmailStr] | None
    """Extra cc, merged into the recipients Graph derives from the original."""

    _check_in_reply_to = field_validator("in_reply_to")(_reject_legacy_entry_id)


class EmailForwardPayload(_EmailCommon):
    kind: Literal["email.forward"]
    in_reply_to: str = Field(min_length=1, max_length=512)
    to: list[EmailStr] = Field(min_length=1)
    cc: list[EmailStr] | None

    _check_in_reply_to = field_validator("in_reply_to")(_reject_legacy_entry_id)


class TeamsPostPayload(BaseModel):
    kind: Literal["teams.post_message"]
    model_config = PAYLOAD_MODEL_CONFIG
    team_id: str = Field(min_length=1)
    channel_id: str = Field(min_length=1)
    body: str
    """HTML. Graph accepts only a limited subset in a channel message."""


class FileUpdatePayload(BaseModel):
    kind: Literal["file.update"]
    model_config = PAYLOAD_MODEL_CONFIG
    site_hostname: str = Field(min_length=1)
    site_path: str = Field(min_length=1)
    library_name: str = Field(min_length=1)
    item_path: str = Field(min_length=1)
    etag: str | None
    """None routes to create-only, a string to update-only. The routing is
    structural: there is no third path and no boolean flag, so an intent cannot
    ask for an unconditional overwrite at all."""

    content_type: str = Field(min_length=1)
    body: str


IntentPayload = Annotated[
    EmailDraftPayload | EmailReplyPayload | EmailForwardPayload | TeamsPostPayload | FileUpdatePayload,
    Field(discriminator="kind"),
]

PAYLOAD_KINDS: tuple[str, ...] = (
    "email.draft",
    "email.reply",
    "email.forward",
    "teams.post_message",
    "file.update",
)
