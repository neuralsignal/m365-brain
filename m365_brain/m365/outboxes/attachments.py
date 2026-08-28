"""Putting files on a draft: inline base64 below a threshold, session above it.

Two behaviours here are the ones a port silently loses.

`isInline` + `contentId` is what makes `<img src="cid:...">` resolve in
Outlook. Without it the image is a normal attachment and the body renders a
broken-image box -- the mail still sends, so nothing fails.

And every path is resolved and existence-checked **before** the first Graph
call. A missing attachment discovered halfway through leaves a draft in the
user's mailbox with some of its files, which is worse than no draft at all.
"""

from __future__ import annotations

import base64
import mimetypes
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from m365_brain.config import UploadConfig
from m365_brain.m365.client import GraphClient
from m365_brain.m365.upload import upload_in_chunks
from m365_brain.storage.exceptions import PathTraversalError

FILE_ATTACHMENT_TYPE = "#microsoft.graph.fileAttachment"
FALLBACK_CONTENT_TYPE = "application/octet-stream"


@dataclass(frozen=True)
class MessageTarget:
    """The draft message an attachment belongs to: endpoint base and message id."""

    base: str
    message_id: str


def resolve_attachment(root: str, path: str) -> Path:
    """Resolve one attachment path against `outboxes.attachment_root`.

    Every resolved path must stay within the root. Absolute paths and `..`
    traversals that escape the boundary raise `PathTraversalError`.
    """
    root_resolved = Path(root).resolve()
    candidate = root_resolved / Path(path)
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root_resolved):
        raise PathTraversalError(f"attachment path {path!r} resolves outside attachment_root {root!r}")
    if not resolved.exists():
        raise FileNotFoundError(f"attachment not found: {path!r} (resolved to {resolved})")
    return resolved


def attach_file(
    client: GraphClient,
    upload: UploadConfig,
    target: MessageTarget,
    file_path: Path,
    is_inline: bool,
    content_id: str | None,
) -> None:
    """Attach one file, choosing the inline or the session path by size.

    Graph caps a single attachment request at roughly 3 MB *encoded*, so the
    raw-side threshold is config (`m365.upload.inline_attachment_max_bytes`)
    rather than a constant somebody would have to recompute.
    """
    raw = file_path.read_bytes()
    content_type = mimetypes.guess_type(str(file_path))[0] or FALLBACK_CONTENT_TYPE
    endpoint = f"{target.base}/messages/{target.message_id}/attachments"

    if len(raw) <= upload.inline_attachment_max_bytes:
        payload: dict[str, Any] = {
            "@odata.type": FILE_ATTACHMENT_TYPE,
            "name": file_path.name,
            "contentType": content_type,
            "contentBytes": base64.b64encode(raw).decode("ascii"),
            "isInline": is_inline,
        }
        if content_id is not None:
            payload["contentId"] = content_id
        client.post(endpoint, payload)
        return

    item: dict[str, Any] = {
        "attachmentType": "file",
        "name": file_path.name,
        "size": len(raw),
        "contentType": content_type,
        "isInline": is_inline,
    }
    if content_id is not None:
        item["contentId"] = content_id
    session = client.post(f"{endpoint}/createUploadSession", {"AttachmentItem": item})
    upload_in_chunks(
        session.json()["uploadUrl"],
        raw,
        upload.chunk_bytes,
        client.config.timeout_seconds,
        client.config.error_message_max_length,
    )
