"""File frontmatter builders (OneDrive and SharePoint).

`content_status` is deliberately not called `conversion_status`. That word
already names a different vocabulary one layer over: `CatalogEntry
.conversion_status` is a file-catalog column whose whole value set is
`index.catalog.conversion_states`, validated on every write, and it describes a
**binary attachment** moving through `index catalog extract`. This key
describes a **drive item's markdown body** and its values are literals this
module owns -- and half of them (`error_too_large`, `error_no_download_url`,
`error_download`) are download outcomes that happen before any conversion is
attempted, so "conversion status" was never what it held.

The two never meet: the catalog only ever sees bytes written through
`StorageBackend.write_bytes`, which no drive-item extractor calls, and `index/`
may not import `m365/` at all. One word over two disjoint value sets is one
word too few -- a `--status` filter, an observation category or a config key
naming either one silently means the other.
"""

from __future__ import annotations

from dataclasses import dataclass

from m365_brain.m365.markdown_writer import now_iso, short_hash, slugify

CONTENT_STATUS = "content_status"
"""The frontmatter key, and the observation category, that carry the above.

Named because `_file_helpers` writes it six times by string key into a dict
this module builds and reads it back for the observation line; a literal at
each site is a rename that half-lands.
"""


@dataclass(frozen=True)
class OneDriveFileData:
    file_name: str
    item_id: str
    size: int
    modified_time: str
    modified_by: str
    parent_path: str
    web_url: str
    content_status: str


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
    content_status: str


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
        CONTENT_STATUS: data.content_status,
        "source": {
            "system": "microsoft365",
            "service": "onedrive",
            "id": data.item_id,
            "url": data.web_url,
            "extracted_at": now_iso(),
            "extractor": "m365-brain/onedrive/1.0",
        },
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
        CONTENT_STATUS: data.content_status,
        "source": {
            "system": "microsoft365",
            "service": "sharepoint",
            "id": data.item_id,
            "url": data.web_url,
            "extracted_at": now_iso(),
            "extractor": "m365-brain/sharepoint/1.0",
        },
    }
