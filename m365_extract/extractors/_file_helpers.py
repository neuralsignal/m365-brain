"""Shared helpers for file-based extractors (OneDrive, SharePoint).

Handles download, conversion, stub generation, and deletion for Graph drive items.
"""

from __future__ import annotations

import fnmatch
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import structlog

from m365_extract.converters.document import convert_document
from m365_extract.graph_client import GraphApiError, GraphClient
from m365_extract.markdown_writer import dumps_markdown, short_hash, slugify
from m365_extract.storage.base import StorageBackend

log = structlog.get_logger()


@dataclass(frozen=True)
class FileProcessingConfig:
    """Groups file-processing parameters passed together through extractors."""

    eager_patterns: list[str]
    convertible_extensions: list[str]
    max_file_size_mb: int
    converters_config: dict


@dataclass(frozen=True)
class FileProcessingContext:
    """Per-run context grouping I/O dependencies with file-processing config."""

    client: GraphClient
    storage: StorageBackend
    file_config: FileProcessingConfig


@dataclass(frozen=True)
class DriveItemMetadata:
    """Common metadata extracted from a Graph API drive item."""

    file_name: str
    item_id: str
    size: int
    modified_time: str
    modified_by: str
    parent_path: str
    web_url: str


def extract_parent_path(parent_reference: dict) -> str:
    """Extract the human-readable parent path from a Graph API parentReference.

    Graph returns paths like '/drive/root:/Documents/Reports'. This strips
    the '/drive/root:' prefix to return 'Documents/Reports'.
    """
    raw_path = parent_reference.get("path", "")
    # Strip the /drive/root: or /drives/{id}/root: prefix
    if ":" in raw_path:
        return raw_path.split(":", 1)[1].lstrip("/")
    return raw_path.lstrip("/")


def build_storage_path(prefix: str, parent_path: str, file_name: str, item_id: str) -> str:
    """Build a deterministic storage path for a file.

    Returns: '{prefix}/{parent-slug}/{name-slug}-{hash}.md'
    """
    parts = [prefix]
    if parent_path:
        # Slugify each path segment individually to preserve hierarchy
        for segment in parent_path.split("/"):
            if segment:
                parts.append(slugify(segment, 80))
    name_slug = slugify(file_name, 80)
    hsh = short_hash(item_id, 6)
    parts.append(f"{name_slug}-{hsh}.md")
    return "/".join(parts)


def should_eager_convert(file_name: str, patterns: list[str]) -> bool:
    """Check if a file name matches any of the eager conversion patterns.

    Uses fnmatch for glob-style matching (e.g., '*.docx', 'report-*.pdf').
    """
    lower_name = file_name.lower()
    return any(fnmatch.fnmatch(lower_name, pattern.lower()) for pattern in patterns)


def process_drive_item(
    ctx: FileProcessingContext,
    item: dict,
    storage_path: str,
    frontmatter: dict,
) -> bool:
    """Process a single drive item: download, convert if eager, or write a stub.

    Returns True if a file was written.
    """
    file_name = item.get("name", "")
    extension = ("." + file_name.rsplit(".", 1)[-1].lower()) if "." in file_name else ""

    is_convertible = extension in ctx.file_config.convertible_extensions
    is_eager = should_eager_convert(file_name, ctx.file_config.eager_patterns)

    if is_convertible and is_eager:
        # Pre-download size check using Graph metadata
        file_size_bytes = item.get("size", 0)
        file_size_mb = file_size_bytes / (1024 * 1024)
        if file_size_mb > ctx.file_config.max_file_size_mb:
            log.warning(
                "file_helpers.file_too_large",
                file=file_name,
                size_mb=round(file_size_mb, 1),
                limit_mb=ctx.file_config.max_file_size_mb,
            )
            frontmatter["conversion_status"] = "error_too_large"
            body = (
                f"# {file_name}\n\nFile is {file_size_mb:.1f} MB, exceeding limit of {ctx.file_config.max_file_size_mb} MB."
            )
            content = dumps_markdown(frontmatter, body)
            ctx.storage.write_file(storage_path, content)
            return True

        download_url = item.get("@microsoft.graph.downloadUrl", "")
        if not download_url:
            # Delta responses often omit @microsoft.graph.downloadUrl.
            # Fetch the item individually to get the download URL.
            item_id = item.get("id", "")
            if item_id:
                try:
                    full_item = ctx.client.get(
                        f"/me/drive/items/{item_id}",
                        params={
                            "$select": "@microsoft.graph.downloadUrl",
                        },
                    )
                    download_url = full_item.get("@microsoft.graph.downloadUrl", "")
                except GraphApiError as exc:
                    log.warning("file_helpers.item_fetch_failed", file=file_name, error=str(exc))

        if not download_url:
            log.warning("file_helpers.no_download_url", file=file_name)
            frontmatter["conversion_status"] = "error_no_download_url"
            body = f"# {file_name}\n\nNo download URL available."
            content = dumps_markdown(frontmatter, body)
            ctx.storage.write_file(storage_path, content)
            return True

        try:
            file_bytes = ctx.client.get_bytes(download_url)
        except GraphApiError as exc:
            log.error("file_helpers.download_failed", file=file_name, error=str(exc))
            frontmatter["conversion_status"] = "error_download"
            body = f"# {file_name}\n\nDownload failed: {exc}"
            content = dumps_markdown(frontmatter, body)
            ctx.storage.write_file(storage_path, content)
            return True

        # Write to tempfile, convert, then clean up
        suffix = extension if extension else ""
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp.write(file_bytes)
            tmp_path = Path(tmp.name)

        try:
            markdown_text = convert_document(
                file_path=tmp_path,
                converters_config=ctx.file_config.converters_config,
            )
            frontmatter["conversion_status"] = "converted"
            body = f"# {file_name}\n\n{markdown_text}"
        except (ImportError, ValueError, OSError) as exc:
            log.error("file_helpers.conversion_failed", file=file_name, error=str(exc))
            frontmatter["conversion_status"] = "error_conversion"
            body = f"# {file_name}\n\nConversion failed: {exc}"
        finally:
            tmp_path.unlink(missing_ok=True)

        content = dumps_markdown(frontmatter, body)
        ctx.storage.write_file(storage_path, content)
        return True

    # Non-eager or non-convertible: write a metadata stub
    frontmatter["conversion_status"] = "pending" if is_convertible else "not_convertible"
    size = item.get("size", 0)
    modified = item.get("lastModifiedDateTime", "")
    body_parts = [f"# {file_name}\n"]
    body_parts.append("## Observations\n")
    body_parts.append(f"- [file_size] {size}")
    body_parts.append(f"- [modified] {modified}")
    body_parts.append(f"- [extension] {extension}")
    body_parts.append(f"- [conversion_status] {frontmatter['conversion_status']}")

    content = dumps_markdown(frontmatter, "\n".join(body_parts))
    ctx.storage.write_file(storage_path, content)
    return True


def iterate_drive_items(
    ctx: FileProcessingContext,
    items: list[dict],
    file_paths: dict[str, str],
    prefix: str,
    build_frontmatter: Callable[[DriveItemMetadata], dict],
) -> int:
    """Iterate over Graph API drive items, filtering, tracking, and processing each one.

    Returns the number of items written.
    """
    written = 0
    for item in items:
        item_id = item.get("id", "")

        if "@removed" in item:
            handle_removed_item(ctx.storage, item_id, file_paths)
            continue

        if "folder" in item:
            continue

        if "file" not in item:
            continue

        file_name = item.get("name", "")
        if not file_name:
            continue

        parent_ref = item.get("parentReference", {})
        parent_path = extract_parent_path(parent_ref)
        storage_path = build_storage_path(prefix, parent_path, file_name, item_id)

        file_paths[item_id] = storage_path

        modified_by_obj = item.get("lastModifiedBy", {}).get("user", {})
        meta = DriveItemMetadata(
            file_name=file_name,
            item_id=item_id,
            size=item.get("size", 0),
            modified_time=item.get("lastModifiedDateTime", ""),
            modified_by=modified_by_obj.get("displayName", ""),
            parent_path=parent_path,
            web_url=item.get("webUrl", ""),
        )

        fm = build_frontmatter(meta)

        if process_drive_item(
            ctx=ctx,
            item=item,
            storage_path=storage_path,
            frontmatter=fm,
        ):
            written += 1

    return written


def handle_removed_item(
    storage: StorageBackend,
    item_id: str,
    file_paths: dict[str, str],
) -> None:
    """Delete the markdown file for a removed drive item."""
    storage_path = file_paths.get(item_id)
    if storage_path:
        storage.delete_file(storage_path)
        del file_paths[item_id]
        log.info("file_helpers.deleted", item_id=item_id, path=storage_path)
