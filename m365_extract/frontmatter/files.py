"""File frontmatter builders (OneDrive and SharePoint)."""

from __future__ import annotations

from m365_extract.markdown_writer import now_iso, short_hash, slugify


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
    slug = slugify(file_name, 80)
    permalink = f"onedrive-{slug}-{short_hash(item_id, 6)}"
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
    slug = slugify(file_name, 80)
    permalink = f"sharepoint-{slug}-{short_hash(item_id, 6)}"
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
