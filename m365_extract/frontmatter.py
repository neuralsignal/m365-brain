"""Per-extractor frontmatter builder functions for Obsidian-compatible markdown."""

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
    slug = slugify(subject)
    permalink = f"email-{date_str}-{slug}-{short_hash(message_id)}"
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


def build_calendar_frontmatter(
    *,
    subject: str,
    event_id: str,
    start_time: str,
    end_time: str,
    location: str,
    organizer_name: str,
    organizer_email: str,
    attendees: list[str],
    is_recurring: bool,
    web_link: str,
) -> dict:
    """Build frontmatter dict for a calendar event."""
    date_str = start_time[:10]
    slug = slugify(subject)
    permalink = f"calendar-{date_str}-{slug}-{short_hash(event_id)}"
    tags = ["calendar"]
    if is_recurring:
        tags.append("recurring")
    fm: dict = {
        "title": subject,
        "permalink": permalink,
        "type": "calendar_event",
        "tags": tags,
        "start": start_time,
        "end": end_time,
        "organizer": organizer_name,
        "organizer_email": organizer_email,
        "attendees": attendees,
        "is_recurring": is_recurring,
        "source": {
            "system": "microsoft365",
            "service": "exchange",
            "id": event_id,
            "url": web_link,
            "extracted_at": now_iso(),
            "extractor": "m365-extract/calendar/1.0",
        },
        "status": "raw",
    }
    if location:
        fm["location"] = location
    return fm


def build_teams_chat_frontmatter(
    *,
    title: str,
    conversation_id: str,
    conversation_type: str,
    participants: list[str],
    last_message_time: str,
) -> dict:
    """Build frontmatter dict for a Teams chat conversation."""
    slug = slugify(title)
    permalink = f"teams-chat-{slug}-{short_hash(conversation_id)}"
    tags = ["teams", f"teams-{conversation_type.lower()}"]
    return {
        "title": title,
        "permalink": permalink,
        "type": "teams_chat",
        "tags": tags,
        "participants": participants,
        "last_message_time": last_message_time,
        "source": {
            "system": "microsoft365",
            "service": "teams",
            "id": conversation_id,
            "extracted_at": now_iso(),
            "extractor": "m365-extract/teams_chats/1.0",
        },
        "status": "raw",
    }


def build_onedrive_frontmatter(
    *,
    file_name: str,
    item_id: str,
    size: int,
    modified_time: str,
    modified_by: str,
    parent_path: str,
    web_url: str,
    conversion_status: str,
) -> dict:
    """Build frontmatter dict for a OneDrive file."""
    extension = "." + file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""
    slug = slugify(file_name)
    permalink = f"onedrive-{slug}-{short_hash(item_id)}"
    tags = ["onedrive"]
    if extension:
        tags.append(extension.lstrip("."))
    return {
        "title": file_name,
        "permalink": permalink,
        "type": "onedrive_file",
        "tags": tags,
        "file_name": file_name,
        "file_size": size,
        "modified": modified_time,
        "modified_by": modified_by,
        "parent_path": parent_path,
        "conversion_status": conversion_status,
        "source": {
            "system": "microsoft365",
            "service": "onedrive",
            "id": item_id,
            "url": web_url,
            "extracted_at": now_iso(),
            "extractor": "m365-extract/onedrive/1.0",
        },
        "status": "raw",
    }


def build_sharepoint_frontmatter(
    *,
    file_name: str,
    item_id: str,
    size: int,
    modified_time: str,
    modified_by: str,
    parent_path: str,
    web_url: str,
    site_name: str,
    drive_name: str,
    conversion_status: str,
) -> dict:
    """Build frontmatter dict for a SharePoint file."""
    extension = "." + file_name.rsplit(".", 1)[-1].lower() if "." in file_name else ""
    slug = slugify(file_name)
    permalink = f"sharepoint-{slug}-{short_hash(item_id)}"
    tags = ["sharepoint"]
    if extension:
        tags.append(extension.lstrip("."))
    return {
        "title": file_name,
        "permalink": permalink,
        "type": "sharepoint_file",
        "tags": tags,
        "file_name": file_name,
        "file_size": size,
        "modified": modified_time,
        "modified_by": modified_by,
        "parent_path": parent_path,
        "site_name": site_name,
        "drive_name": drive_name,
        "conversion_status": conversion_status,
        "source": {
            "system": "microsoft365",
            "service": "sharepoint",
            "id": item_id,
            "url": web_url,
            "extracted_at": now_iso(),
            "extractor": "m365-extract/sharepoint/1.0",
        },
        "status": "raw",
    }


def build_contact_frontmatter(
    *,
    display_name: str,
    contact_id: str,
    email_addresses: list[str],
    phones: list[str],
    company: str,
    job_title: str,
    department: str,
    categories: list[str],
) -> dict:
    """Build frontmatter dict for a contact."""
    slug = slugify(display_name)
    permalink = f"contact-{slug}-{short_hash(contact_id)}"
    tags = ["contact"]
    tags.extend(c.lower().replace(" ", "-") for c in categories)
    fm: dict = {
        "title": display_name,
        "permalink": permalink,
        "type": "contact",
        "tags": tags,
        "source": {
            "system": "microsoft365",
            "service": "people",
            "id": contact_id,
            "extracted_at": now_iso(),
            "extractor": "m365-extract/contacts/1.0",
        },
        "status": "raw",
    }
    if email_addresses:
        fm["email"] = email_addresses
    if phones:
        fm["phone"] = phones
    if company:
        fm["company"] = company
    if job_title:
        fm["job_title"] = job_title
    if department:
        fm["department"] = department
    return fm


def build_directory_user_frontmatter(
    *,
    display_name: str,
    user_id: str,
    email: str,
    upn: str,
    job_title: str,
    department: str,
    office: str,
    city: str,
    manager_link: str,
    direct_reports_links: list[str],
) -> dict:
    """Build frontmatter dict for a directory user."""
    slug = slugify(display_name)
    permalink = f"directory-{slug}-{short_hash(user_id)}"
    tags = ["directory"]
    if department:
        tags.append(department.lower().replace(" ", "-"))
    fm: dict = {
        "title": display_name,
        "permalink": permalink,
        "type": "directory_user",
        "tags": tags,
        "email": email,
        "upn": upn,
        "source": {
            "system": "microsoft365",
            "service": "directory",
            "id": user_id,
            "extracted_at": now_iso(),
            "extractor": "m365-extract/directory/1.0",
        },
        "status": "raw",
    }
    if job_title:
        fm["job_title"] = job_title
    if department:
        fm["department"] = department
    if office:
        fm["office"] = office
    if city:
        fm["city"] = city
    if manager_link:
        fm["manager"] = manager_link
    if direct_reports_links:
        fm["direct_reports"] = direct_reports_links
    return fm


def build_teams_channel_frontmatter(
    *,
    team_name: str,
    channel_name: str,
    channel_id: str,
    last_message_time: str,
) -> dict:
    """Build frontmatter dict for a Teams channel."""
    slug = slugify(f"{team_name}-{channel_name}")
    permalink = f"teams-channel-{slug}-{short_hash(channel_id)}"
    return {
        "title": f"{team_name} / {channel_name}",
        "permalink": permalink,
        "type": "teams_channel",
        "tags": ["teams", "teams-channel"],
        "team": team_name,
        "channel": channel_name,
        "last_message_time": last_message_time,
        "source": {
            "system": "microsoft365",
            "service": "teams",
            "id": channel_id,
            "extracted_at": now_iso(),
            "extractor": "m365-extract/teams_channels/1.0",
        },
        "status": "raw",
    }
