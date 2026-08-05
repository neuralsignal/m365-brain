"""File frontmatter builders (OneDrive and SharePoint)."""

from __future__ import annotations

from dataclasses import dataclass

from m365_brain.m365.markdown_writer import now_iso, short_hash, slugify


@dataclass(frozen=True)
class OneDriveFileData:
    file_name: str
    item_id: str
    size: int
    modified_time: str
    modified_by: str
    parent_path: str
    web_url: str
    conversion_status: str


@dataclass(frozen=True)
class SharePointFileData:
    file_name: str
    item_id: str
    size: int
    modified_time: str
    modified_by: str
    parent_path: str
    web_url: str
    site_name: str
    drive_name: str
    conversion_status: str


def build_onedrive_frontmatter(data: OneDriveFileData) -> dict:
    """Build frontmatter dict for a OneDrive file."""
    extension = "." + data.file_name.rsplit(".", 1)[-1].lower() if "." in data.file_name else ""
    slug = slugify(data.file_name, 80)
    permalink = f"onedrive-{slug}-{short_hash(data.item_id, 6)}"
    tags = ["onedrive"]
    if extension:
        tags.append(extension.lstrip("."))
    return {
        "title": data.file_name,
        "permalink": permalink,
        "type": "onedrive_file",
        "tags": tags,
        "file_name": data.file_name,
        "file_size": data.size,
        "modified": data.modified_time,
        "modified_by": data.modified_by,
        "parent_path": data.parent_path,
        "conversion_status": data.conversion_status,
        "source": {
            "system": "microsoft365",
            "service": "onedrive",
            "id": data.item_id,
            "url": data.web_url,
            "extracted_at": now_iso(),
            "extractor": "m365-brain/onedrive/1.0",
        },
        "status": "raw",
    }


def build_sharepoint_frontmatter(data: SharePointFileData) -> dict:
    """Build frontmatter dict for a SharePoint file."""
    extension = "." + data.file_name.rsplit(".", 1)[-1].lower() if "." in data.file_name else ""
    slug = slugify(data.file_name, 80)
    permalink = f"sharepoint-{slug}-{short_hash(data.item_id, 6)}"
    tags = ["sharepoint"]
    if extension:
        tags.append(extension.lstrip("."))
    return {
        "title": data.file_name,
        "permalink": permalink,
        "type": "sharepoint_file",
        "tags": tags,
        "file_name": data.file_name,
        "file_size": data.size,
        "modified": data.modified_time,
        "modified_by": data.modified_by,
        "parent_path": data.parent_path,
        "site_name": data.site_name,
        "drive_name": data.drive_name,
        "conversion_status": data.conversion_status,
        "source": {
            "system": "microsoft365",
            "service": "sharepoint",
            "id": data.item_id,
            "url": data.web_url,
            "extracted_at": now_iso(),
            "extractor": "m365-brain/sharepoint/1.0",
        },
        "status": "raw",
    }
