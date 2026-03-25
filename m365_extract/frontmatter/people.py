"""People frontmatter builders (contacts and directory users)."""

from __future__ import annotations

from m365_extract.markdown_writer import now_iso, short_hash, slugify


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
    slug = slugify(display_name, 80)
    permalink = f"contact-{slug}-{short_hash(contact_id, 6)}"
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
    slug = slugify(display_name, 80)
    permalink = f"directory-{slug}-{short_hash(user_id, 6)}"
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
