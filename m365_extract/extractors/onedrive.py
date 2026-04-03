"""OneDrive extractor — syncs files via Graph API delta queries.

Uses /me/drive/root/delta for incremental file sync.
Requires Files.Read.All permission.
"""

from __future__ import annotations

from datetime import UTC, datetime

import structlog

from m365_extract.config import OneDriveExtractorConfig
from m365_extract.extractors._file_helpers import (
    build_storage_path,
    extract_parent_path,
    handle_removed_item,
    process_drive_item,
)
from m365_extract.frontmatter import build_onedrive_frontmatter
from m365_extract.graph_client import GraphClient
from m365_extract.storage.base import StorageBackend

log = structlog.get_logger()

name = "onedrive"
required_scopes = ["Files.Read.All"]


def run(
    client: GraphClient,
    storage: StorageBackend,
    state: dict,
    config: OneDriveExtractorConfig,
    converters_config: dict,
) -> tuple[dict, int]:
    """Extract OneDrive files using delta queries.

    Returns (updated_state, items_written).
    """
    delta_link = state.get("delta_link")
    file_paths: dict[str, str] = state.get("file_paths", {})

    path = "/me/drive/root/delta"
    params = {
        "$select": "id,name,size,file,folder,parentReference,lastModifiedDateTime,lastModifiedBy,webUrl,@microsoft.graph.downloadUrl"
    }

    items, new_delta_link = client.get_delta(path, delta_link, params=params, max_pages=client.max_pages)

    if new_delta_link:
        state["delta_link"] = new_delta_link

    written = 0
    for item in items:
        item_id = item.get("id", "")

        # Handle removed items
        if "@removed" in item:
            handle_removed_item(storage, item_id, file_paths)
            continue

        # Skip folders
        if "folder" in item:
            continue

        # Skip items without file metadata
        if "file" not in item:
            continue

        file_name = item.get("name", "")
        if not file_name:
            continue

        parent_ref = item.get("parentReference", {})
        parent_path = extract_parent_path(parent_ref)
        storage_path = build_storage_path("onedrive", parent_path, file_name, item_id)

        # Track file paths for deletion
        file_paths[item_id] = storage_path

        # Extract metadata
        size = item.get("size", 0)
        modified = item.get("lastModifiedDateTime", "")
        modified_by_obj = item.get("lastModifiedBy", {}).get("user", {})
        modified_by = modified_by_obj.get("displayName", "")
        web_url = item.get("webUrl", "")

        fm = build_onedrive_frontmatter(
            file_name=file_name,
            item_id=item_id,
            size=size,
            modified_time=modified,
            modified_by=modified_by,
            parent_path=parent_path,
            web_url=web_url,
            conversion_status="pending",
        )

        if process_drive_item(
            client=client,
            storage=storage,
            item=item,
            storage_path=storage_path,
            frontmatter=fm,
            eager_patterns=config.eager_convert_patterns,
            convertible_extensions=config.convertible_extensions,
            max_file_size_mb=config.max_file_size_mb,
            converters_config=converters_config,
        ):
            written += 1

    state["file_paths"] = file_paths
    state["last_sync"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    log.info("onedrive.sync_complete", written=written)
    return state, written
