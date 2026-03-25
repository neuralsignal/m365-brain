"""Email frontmatter builder."""

from __future__ import annotations

from m365_extract.markdown_writer import now_iso, short_hash, slugify


def build_email_frontmatter(
    *,
    subject: str,
    message_id: str,
    received_time: str,
    folder: str,
    sender_address: str,
    sender_name: str,
    to_recipients: list[str],
    importance: str,
    has_attachments: bool,
    web_link: str,
) -> dict:
    """Build frontmatter dict for an email."""
    date_str = received_time[:10]
    slug = slugify(subject, 80)
    permalink = f"email-{date_str}-{slug}-{short_hash(message_id, 6)}"
    return {
        "title": subject,
        "permalink": permalink,
        "type": "email",
        "tags": ["email", folder.lower().replace(" ", "-")],
        "sender": sender_address,
        "sender_name": sender_name,
        "to": to_recipients,
        "date": received_time,
        "folder": folder,
        "importance": importance,
        "has_attachments": has_attachments,
        "source": {
            "system": "microsoft365",
            "service": "exchange",
            "id": message_id,
            "url": web_link,
            "extracted_at": now_iso(),
            "extractor": "m365-extract/email/1.0",
        },
        "status": "raw",
    }
