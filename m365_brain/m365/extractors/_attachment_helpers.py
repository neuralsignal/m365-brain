"""Helpers for downloading and converting email attachments."""

from __future__ import annotations

import base64
import binascii
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import structlog

from m365_brain.config import EmailExtractorConfig
from m365_brain.m365.client import GraphApiError, GraphClient
from m365_brain.m365.converters.document import DocumentConversionError, convert_document
from m365_brain.storage.base import StorageBackend
from m365_brain.storage.exceptions import StorageError

if TYPE_CHECKING:
    from m365_brain.m365.extractors.base import ExtractorContext

log = structlog.get_logger()


def download_attachments(
    client: GraphClient,
    storage: StorageBackend,
    endpoint_base: str,
    message_id: str,
    email_dir: str,
    config: EmailExtractorConfig,
    ctx: ExtractorContext,
) -> None:
    """Download email attachments and optionally convert them to markdown."""
    path = f"{endpoint_base}/messages/{message_id}/attachments"
    params = {"$top": "20"}
    try:
        for att in client.get_paginated(path, params, max_pages=5):
            att_name = att.get("name", "")
            if not att_name or ":" in att_name:
                continue
            att_name = Path(att_name).name
            if not att_name:
                continue
            if att.get("isInline", False):
                continue
            size = att.get("size", 0)
            if size > config.max_attachment_size_mb * 1024 * 1024:
                log.warning("email.attachment_too_large", name=att_name, size_mb=size // (1024 * 1024))
                continue
            download_url = att.get("@microsoft.graph.downloadUrl")
            content_bytes_b64 = att.get("contentBytes")
            if not download_url and not content_bytes_b64:
                log.warning("email.attachment_no_download_url", name=att_name)
                continue
            try:
                if download_url:
                    data = client.get_bytes(download_url)
                else:
                    data = base64.b64decode(content_bytes_b64)
                storage.write_bytes(ctx.paths.attachment(email_dir, att_name), data)
                ext = Path(att_name).suffix.lower()
                if ext in config.attachment_convert_extensions:
                    convert_and_store(
                        storage,
                        data,
                        att_name,
                        ctx.paths.converted_attachment(email_dir, f"{att_name}.md"),
                        ctx.converters,
                    )
            except (GraphApiError, httpx.TransportError, binascii.Error, StorageError, OSError) as exc:
                log.warning("email.attachment_download_failed", name=att_name, error=str(exc))
    except (GraphApiError, httpx.TransportError) as exc:
        log.warning("email.attachments_fetch_failed", message_id=message_id, error=str(exc))


def convert_and_store(
    storage: StorageBackend,
    data: bytes,
    source_name: str,
    target_path: str,
    converters_config: dict,
) -> bool:
    """Convert an attachment binary to markdown and write to ``target_path``.

    ``source_name`` provides the original filename so the converter can pick a
    backend by suffix; ``target_path`` is the absolute storage path the
    converted markdown should be written to (including the ``.md`` extension).
    Returns True when the converted file was written, False when conversion
    failed (logged; callers must not record a converted-file link).
    """
    suffix = Path(source_name).suffix
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp_path = Path(tmp.name)
            tmp_path.write_bytes(data)
        md_content = convert_document(tmp_path, converters_config)
        storage.write_file(target_path, md_content)
        return True
    except (OSError, ImportError, StorageError, DocumentConversionError) as exc:
        log.warning("attachment.convert_failed", name=source_name, error=str(exc))
        return False
    finally:
        if tmp_path is not None and tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
