"""Writing a document-library file from an intent.

The whole executor is a two-way branch on `payload.etag`, and that is the
point: `None` routes to `create_file`, a string routes to `update_file`, and
there is no third path because `m365/files.py` exposes no function that writes
without one condition or the other. An intent cannot ask for an unconditional
overwrite -- not because this module refuses, but because there is nothing to
call.
"""

from __future__ import annotations

from dataclasses import dataclass

import structlog

from m365_brain.config import UploadConfig
from m365_brain.m365.client import GraphClient
from m365_brain.m365.files import FilePayload, create_file, resolve_drive_id, resolve_site_id, update_file
from m365_brain.vault.dispatch import DispatchResult, GraphOp
from m365_brain.vault.intent import IntentEnvelope

log = structlog.get_logger()

FILE_UPDATE_KIND = "file.update"


class FileIntentError(Exception):
    """The intent is not a file write."""


@dataclass(frozen=True)
class FileUpdateOutbox:
    """Resolves the site and library per intent, then creates or updates."""

    name: str
    client: GraphClient
    upload: UploadConfig

    declared_ops: frozenset[GraphOp] = frozenset({GraphOp.PUT_FILE})

    def execute(self, envelope: IntentEnvelope) -> DispatchResult:
        payload = envelope.payload
        if payload.kind != FILE_UPDATE_KIND:
            raise FileIntentError(f"outbox {self.name!r} received a {payload.kind!r} payload")

        site_id = resolve_site_id(self.client, payload.site_hostname, payload.site_path)
        drive_id, library_is_folder = resolve_drive_id(self.client, site_id, payload.library_name)
        # A library that is a folder inside the default drive needs its name
        # back on the front of the item path; one that is its own drive does not.
        item_path = f"{payload.library_name}/{payload.item_path}" if library_is_folder else payload.item_path
        file_payload = FilePayload(payload.body.encode("utf-8"), payload.content_type)

        if payload.etag is None:
            etag = create_file(self.client, self.upload, drive_id, item_path, file_payload)
            log.info("outbox.files.created", item_path=item_path, etag=etag)
        else:
            etag = update_file(self.client, self.upload, drive_id, item_path, file_payload, payload.etag)
            log.info("outbox.files.updated", item_path=item_path, etag=etag)

        # A drive item has no message id. The new eTag is what a caller needs
        # to write again, so it is what travels back on the receipt.
        return DispatchResult(graph_message_id=etag)
