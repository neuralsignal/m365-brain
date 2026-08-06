"""Email frontmatter builder.

Three of these keys are load-bearing for `ops triage` rather than for a reader,
and all three are scalars for the same reason. `m365_brain/parsers/document.py`
promotes a **scalar** frontmatter key to an observation and leaves a list or a
dict in metadata, and metadata is not retrievable per entity -- `EntityRef`
carries none and `IndexBackend` offers no accessor for it, only a filter. So a
fact that an operation has to *read* is written as a scalar or it is not
readable at all:

* `conversation_id` is what pairs a sent reply with the message it answers.
  Graph returns `conversationId` on every message; without it here, "has this
  been replied to?" has no answer in the index.
* `message_id` duplicates `source.id`, and does so deliberately: `source` is a
  dict, so its contents are metadata and unreadable per entity. An intent's
  `in_reply_to` is a *message* id, so without this key the "a human threw the
  draft away" clause of `ops triage` has nothing to compare against and can
  never fire.
* `to` is a joined string, not the list Graph hands over. The list rendered
  fine and read back as nothing. `ops.names.email_addresses` splits the
  addresses out again, which is what it was written for.

The reader names both categories in `ops.triage.fields`, so the *names* below
are this extractor's vocabulary rather than the library's.
"""

from __future__ import annotations

from dataclasses import dataclass

from m365_brain.m365.frontmatter._tags import tag_slug
from m365_brain.m365.markdown_writer import now_iso, short_hash, slugify


@dataclass(frozen=True)
class EmailData:
    subject: str
    message_id: str
    conversation_id: str
    received_time: str
    folder: str
    mailbox: str
    sender_address: str
    sender_name: str
    to_recipients: list[str]
    importance: str
    has_attachments: bool
    web_link: str


def build_email_frontmatter(data: EmailData) -> dict:
    """Build frontmatter dict for an email."""
    date_str = data.received_time[:10]
    slug = slugify(data.subject, 80)
    permalink = f"email-{date_str}-{slug}-{short_hash(data.message_id, 6)}"
    tags = ["email"]
    folder_tag = tag_slug(data.folder, 80)
    if folder_tag:
        tags.append(folder_tag)
    return {
        "title": data.subject,
        "permalink": permalink,
        "type": "email",
        "tags": tags,
        "sender": data.sender_address,
        "sender_name": data.sender_name,
        "to": ", ".join(data.to_recipients),
        "date": data.received_time,
        "conversation_id": data.conversation_id,
        "message_id": data.message_id,
        "folder": data.folder,
        "mailbox": data.mailbox,
        "importance": data.importance,
        "has_attachments": data.has_attachments,
        "source": {
            "system": "microsoft365",
            "service": "exchange",
            "id": data.message_id,
            "url": data.web_link,
            "extracted_at": now_iso(),
            "extractor": "m365-brain/email/1.3",
        },
    }
