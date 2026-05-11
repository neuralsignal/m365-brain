"""People frontmatter builders (contacts and directory users)."""

from __future__ import annotations

from dataclasses import dataclass

from m365_extract.markdown_writer import now_iso, short_hash, slugify


@dataclass(frozen=True)
class ContactData:
    display_name: str
    contact_id: str
    email_addresses: list[str]
    phones: list[str]
    company: str
    job_title: str
    department: str
    categories: list[str]


@dataclass(frozen=True)
class DirectoryUserData:
    display_name: str
    user_id: str
    email: str
    upn: str
    job_title: str
    department: str
    office: str
    city: str
    manager_link: str
    direct_reports_links: list[str]


def build_contact_frontmatter(data: ContactData) -> dict:
    """Build frontmatter dict for a contact."""
    slug = slugify(data.display_name, 80)
    permalink = f"contact-{slug}-{short_hash(data.contact_id, 6)}"
    tags = ["contact"]
    tags.extend(c.lower().replace(" ", "-") for c in data.categories)
    fm: dict = {
        "title": data.display_name,
        "permalink": permalink,
        "type": "contact",
        "tags": tags,
        "source": {
            "system": "microsoft365",
            "service": "people",
            "id": data.contact_id,
            "extracted_at": now_iso(),
            "extractor": "m365-extract/contacts/1.0",
        },
        "status": "raw",
    }
    if data.email_addresses:
        fm["email"] = data.email_addresses
    if data.phones:
        fm["phone"] = data.phones
    if data.company:
        fm["company"] = data.company
    if data.job_title:
        fm["job_title"] = data.job_title
    if data.department:
        fm["department"] = data.department
    return fm


def build_directory_user_frontmatter(data: DirectoryUserData) -> dict:
    """Build frontmatter dict for a directory user."""
    slug = slugify(data.display_name, 80)
    permalink = f"directory-{slug}-{short_hash(data.user_id, 6)}"
    tags = ["directory"]
    if data.department:
        tags.append(data.department.lower().replace(" ", "-"))
    fm: dict = {
        "title": data.display_name,
        "permalink": permalink,
        "type": "directory_user",
        "tags": tags,
        "email": data.email,
        "upn": data.upn,
        "source": {
            "system": "microsoft365",
            "service": "directory",
            "id": data.user_id,
            "extracted_at": now_iso(),
            "extractor": "m365-extract/directory/1.0",
        },
        "status": "raw",
    }
    if data.job_title:
        fm["job_title"] = data.job_title
    if data.department:
        fm["department"] = data.department
    if data.office:
        fm["office"] = data.office
    if data.city:
        fm["city"] = data.city
    if data.manager_link:
        fm["manager"] = data.manager_link
    if data.direct_reports_links:
        fm["direct_reports"] = data.direct_reports_links
    return fm
