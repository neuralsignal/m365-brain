"""Email frontmatter builder."""

from __future__ import annotations

from dataclasses import dataclass

from m365_brain.m365.frontmatter._tags import tag_slug
from m365_brain.m365.markdown_writer import now_iso, short_hash, slugify


@dataclass(frozen=True)
class EmailData:
    subject: str
    message_id: str
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
        "to": data.to_recipients,
        "date": data.received_time,
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
            "extractor": "m365-brain/email/1.1",
        },
        "status": "raw",
    }
