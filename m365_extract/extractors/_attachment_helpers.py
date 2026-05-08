"""Helpers for downloading and converting email attachments."""

from __future__ import annotations

import base64
import binascii
import tempfile
from pathlib import Path

import httpx
import structlog

from m365_extract.config import EmailExtractorConfig
from m365_extract.converters.document import convert_document
from m365_extract.graph_client import GraphApiError, GraphClient
from m365_extract.storage.base import StorageBackend
from m365_extract.storage.exceptions import StorageError

log = structlog.get_logger()


def download_attachments(
    client: GraphClient,
    storage: StorageBackend,
    message_id: str,
    email_dir: str,
    config: EmailExtractorConfig,
    converters_config: dict,
) -> None:
    """Download email attachments and optionally convert them to markdown."""
    path = f"/me/messages/{message_id}/attachments"
    params = {"$top": "20"}
    try:
        for att in client.get_paginated(path, params, max_pages=5):
            att_name = att.get("name", "")
            if not att_name or ":" in att_name:
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
                storage.write_bytes(f"{email_dir}/attachments/{att_name}", data)
                ext = Path(att_name).suffix.lower()
                if ext in config.attachment_convert_extensions:
                    convert_and_store(storage, data, att_name, email_dir, converters_config)
            except (GraphApiError, httpx.TransportError, binascii.Error, StorageError, OSError) as exc:
                log.warning("email.attachment_download_failed", name=att_name, error=str(exc))
    except (GraphApiError, httpx.TransportError) as exc:
        log.warning("email.attachments_fetch_failed", message_id=message_id, error=str(exc))


def convert_and_store(
    storage: StorageBackend,
    data: bytes,
    att_name: str,
    email_dir: str,
    converters_config: dict,
) -> None:
    """Convert an attachment binary to markdown and write to storage."""
    suffix = Path(att_name).suffix
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
            tmp_path = Path(tmp.name)
            tmp_path.write_bytes(data)
        md_content = convert_document(tmp_path, converters_config)
        storage.write_file(f"{email_dir}/attachments_converted/{att_name}.md", md_content)
    except (OSError, ImportError, StorageError) as exc:
        log.warning("email.attachment_convert_failed", name=att_name, error=str(exc))
    finally:
        if tmp_path is not None and tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
