"""SharePoint extractor — syncs files from followed sites via Graph API.

Discovers sites via /me/followedSites, enumerates drives per site,
then uses delta queries per drive for incremental file sync.
Requires Sites.Read.All permission.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import structlog

from m365_extract.config import SharePointExtractorConfig
from m365_extract.extractors._file_helpers import (
    DriveItemMetadata,
    FileProcessingConfig,
    iterate_drive_items,
)
from m365_extract.frontmatter import SharePointFileData, build_sharepoint_frontmatter
from m365_extract.graph_client import GraphApiError, GraphClient
from m365_extract.markdown_writer import slugify
from m365_extract.storage.base import StorageBackend

log = structlog.get_logger()


@dataclass(frozen=True)
class SiteDriveRef:
    """Identifies a specific drive within a SharePoint site."""

    site_id: str
    site_name: str
    drive_id: str
    drive_name: str


name = "sharepoint"
required_scopes = ["Sites.Read.All"]


def run(
    client: GraphClient,
    storage: StorageBackend,
    state: dict,
    config: SharePointExtractorConfig,
    converters_config: dict,
) -> tuple[dict, int]:
    """Extract SharePoint files from followed sites.

    Returns (updated_state, items_written).
    """
    # Discover followed sites
    sites = list(client.get_paginated("/me/followedSites", params={"$top": "100"}, max_pages=client.max_pages))
    log.info("sharepoint.fetched_sites", count=len(sites))

    file_config = FileProcessingConfig(
        eager_patterns=config.eager_convert_patterns,
        convertible_extensions=config.convertible_extensions,
        max_file_size_mb=config.max_file_size_mb,
        converters_config=converters_config,
    )

    written = 0
    for site in sites:
        site_id = site.get("id", "")
        site_name = site.get("displayName", "Unknown Site")

        # Enumerate drives for this site
        try:
            drives = list(
                client.get_paginated(
                    f"/sites/{site_id}/drives",
                    params={"$top": "100"},
                    max_pages=client.max_pages,
                )
            )
        except GraphApiError as exc:
            log.warning("sharepoint.drives_fetch_failed", site=site_name, error=str(exc))
            continue

        for drive in drives:
            drive_ref = SiteDriveRef(
                site_id=site_id,
                site_name=site_name,
                drive_id=drive.get("id", ""),
                drive_name=drive.get("name", "Documents"),
            )

            count = _sync_drive(
                client=client,
                storage=storage,
                state=state,
                drive_ref=drive_ref,
                file_config=file_config,
            )
            written += count

    state["last_sync"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    log.info("sharepoint.sync_complete", written=written)
    return state, written


def _sync_drive(
    client: GraphClient,
    storage: StorageBackend,
    state: dict,
    drive_ref: SiteDriveRef,
    file_config: FileProcessingConfig,
) -> int:
    """Sync a single SharePoint drive using delta queries. Returns items written."""
    delta_key = f"delta_{drive_ref.site_id}_{drive_ref.drive_id}"
    file_paths_key = f"file_paths_{drive_ref.site_id}_{drive_ref.drive_id}"
    delta_link = state.get(delta_key)
    file_paths: dict[str, str] = state.get(file_paths_key, {})

    path = f"/drives/{drive_ref.drive_id}/root/delta"
    params = {
        "$select": "id,name,size,file,folder,parentReference,lastModifiedDateTime,lastModifiedBy,webUrl,@microsoft.graph.downloadUrl"
    }

    try:
        items, new_delta_link = client.get_delta(path, delta_link, params=params, max_pages=client.max_pages)
    except GraphApiError as exc:
        log.warning(
            "sharepoint.delta_failed",
            site=drive_ref.site_name,
            drive=drive_ref.drive_name,
            error=str(exc),
        )
        return 0

    if new_delta_link:
        state[delta_key] = new_delta_link

    site_slug = slugify(drive_ref.site_name, 80)
    drive_slug = slugify(drive_ref.drive_name, 80)
    prefix = f"sharepoint/{site_slug}/{drive_slug}"

    def _build_fm(meta: DriveItemMetadata) -> dict:
        return build_sharepoint_frontmatter(
            SharePointFileData(
                file_name=meta.file_name,
                item_id=meta.item_id,
                size=meta.size,
                modified_time=meta.modified_time,
                modified_by=meta.modified_by,
                parent_path=meta.parent_path,
                web_url=meta.web_url,
                site_name=drive_ref.site_name,
                drive_name=drive_ref.drive_name,
                conversion_status="pending",
            )
        )

    written = iterate_drive_items(
        client=client,
        storage=storage,
        items=items,
        file_paths=file_paths,
        prefix=prefix,
        build_frontmatter=_build_fm,
        file_config=file_config,
    )

    state[file_paths_key] = file_paths
    return written
