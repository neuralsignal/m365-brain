"""OneDrive extractor — syncs files via Graph API delta queries.

Uses /me/drive/root/delta for incremental file sync.
Requires Files.Read.All permission.
"""

from __future__ import annotations

from datetime import UTC, datetime

import structlog

from m365_brain.config import OneDriveExtractorConfig
from m365_brain.m365.client import GraphClient
from m365_brain.m365.extractors._file_helpers import (
    DriveItemMetadata,
    FileProcessingConfig,
    FileProcessingContext,
    iterate_drive_items,
)
from m365_brain.m365.extractors.base import ExtractorContext
from m365_brain.m365.frontmatter import OneDriveFileData, build_onedrive_frontmatter
from m365_brain.storage.base import StorageBackend

log = structlog.get_logger()

name = "onedrive"
required_scopes = ["Files.Read.All"]


def run(
    client: GraphClient,
    storage: StorageBackend,
    state: dict,
    config: OneDriveExtractorConfig,
    ctx: ExtractorContext,
) -> tuple[dict, int]:
    """Extract OneDrive files using delta queries.

    Returns (updated_state, items_written).
    """
    file_config = FileProcessingConfig(
        eager_patterns=config.eager_convert_patterns,
        convertible_extensions=config.convertible_extensions,
        max_file_size_mb=config.max_file_size_mb,
        converters_config=ctx.converters,
    )

    delta_link = state.get("delta_link")
    file_paths: dict[str, str] = state.get("file_paths", {})

    path = "/me/drive/root/delta"
    params = {
        "$select": "id,name,size,file,folder,parentReference,lastModifiedDateTime,lastModifiedBy,webUrl,@microsoft.graph.downloadUrl"
    }

    items, new_delta_link = client.get_delta(path, delta_link, params=params, max_pages=client.max_pages)

    if new_delta_link:
        state["delta_link"] = new_delta_link

    def _build_fm(meta: DriveItemMetadata) -> dict:
        return build_onedrive_frontmatter(
            OneDriveFileData(
                file_name=meta.file_name,
                item_id=meta.item_id,
                size=meta.size,
                modified_time=meta.modified_time,
                modified_by=meta.modified_by,
                parent_path=meta.parent_path,
                web_url=meta.web_url,
                content_status="pending",
            )
        )

    written = iterate_drive_items(
        ctx=FileProcessingContext(
            client=client,
            storage=storage,
            file_config=file_config,
            removal=ctx.removal,
            extractor=name,
        ),
        items=items,
        file_paths=file_paths,
        prefix=ctx.paths.inbox_root(name),
        build_frontmatter=_build_fm,
    )

    state["file_paths"] = file_paths
    state["last_sync"] = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    log.info("onedrive.sync_complete", written=written)
    return state, written
