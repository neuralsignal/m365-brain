"""Attachment upload: the inline path, the session path, and path resolution."""

from __future__ import annotations

import base64
import json

import pytest

from m365_brain.config import UploadConfig
from m365_brain.m365.outboxes.attachments import attach_file, resolve_attachment

from .conftest import LARGE_ATTACHMENT_BYTES


class TestResolution:
    def test_a_relative_path_resolves_against_the_configured_root(self, attachment_root):
        resolved = resolve_attachment(str(attachment_root), "doc.txt")

        assert resolved == (attachment_root / "doc.txt").resolve()

    def test_an_absolute_path_is_taken_as_given(self, attachment_root):
        absolute = str((attachment_root / "doc.txt").resolve())

        assert resolve_attachment("/somewhere/else", absolute) == (attachment_root / "doc.txt").resolve()

    def test_a_missing_file_names_both_what_was_written_and_what_it_resolved_to(self, attachment_root):
        with pytest.raises(FileNotFoundError) as excinfo:
            resolve_attachment(str(attachment_root), "deck.pdf")

        assert "'deck.pdf'" in str(excinfo.value)
        assert str(attachment_root) in str(excinfo.value)


class TestInlinePath:
    def test_a_small_file_is_posted_as_base64(self, client, upload, attachment_root, recorded):
        attach_file(client, upload, "/me", "MSG-1", attachment_root / "doc.txt", False, None)

        assert len(recorded) == 1
        body = json.loads(recorded[0].content)
        assert body["@odata.type"] == "#microsoft.graph.fileAttachment"
        assert body["name"] == "doc.txt"
        assert base64.b64decode(body["contentBytes"]) == b"a small attachment"
        assert body["isInline"] is False
        assert "contentId" not in body

    def test_an_inline_attachment_carries_its_content_id(self, client, upload, attachment_root, recorded):
        """Without `isInline` + `contentId`, `<img src="cid:...">` resolves to
        nothing and Outlook renders a broken-image box."""
        attach_file(client, upload, "/me", "MSG-1", attachment_root / "banner.png", True, "banner")

        body = json.loads(recorded[0].content)
        assert body["isInline"] is True
        assert body["contentId"] == "banner"

    def test_the_mime_type_is_sniffed_from_the_name(self, client, upload, attachment_root, recorded):
        attach_file(client, upload, "/me", "MSG-1", attachment_root / "banner.png", True, "banner")

        assert json.loads(recorded[0].content)["contentType"] == "image/png"

    def test_an_unrecognised_extension_falls_back(self, client, upload, attachment_root, recorded):
        blob = attachment_root / "thing.unknownext"
        blob.write_bytes(b"data")

        attach_file(client, upload, "/me", "MSG-1", blob, False, None)

        assert json.loads(recorded[0].content)["contentType"] == "application/octet-stream"

    def test_the_shared_mailbox_base_is_used_verbatim(self, client, upload, attachment_root, recorded):
        attach_file(client, upload, "/users/shared@example.com", "MSG-1", attachment_root / "doc.txt", False, None)

        assert recorded[0].url.path.startswith("/v1.0/users/shared@example.com/messages/MSG-1/attachments")


class TestSessionPath:
    def test_a_file_above_the_ceiling_opens_an_upload_session(self, client, upload, attachment_root, recorded):
        attach_file(client, upload, "/me", "MSG-1", attachment_root / "large.bin", False, None)

        assert [request.method for request in recorded] == ["POST", "PUT"]
        item = json.loads(recorded[0].content)["AttachmentItem"]
        assert item["name"] == "large.bin"
        assert item["size"] == len(LARGE_ATTACHMENT_BYTES)
        assert recorded[1].headers["Content-Range"] == f"bytes 0-{len(LARGE_ATTACHMENT_BYTES) - 1}/{item['size']}"

    def test_an_inline_content_id_survives_onto_the_session_item(self, client, upload, attachment_root, recorded):
        attach_file(client, upload, "/me", "MSG-1", attachment_root / "large.bin", True, "poster")

        item = json.loads(recorded[0].content)["AttachmentItem"]
        assert item["isInline"] is True
        assert item["contentId"] == "poster"

    def test_the_ceiling_is_config(self, client, attachment_root, recorded):
        """Lowering it moves a file that used to go inline onto the session
        path -- which is exactly what an operator on a stricter tenant needs."""
        tiny = UploadConfig(inline_attachment_max_bytes=4, simple_upload_max_bytes=4096, chunk_bytes=320 * 1024)

        attach_file(client, tiny, "/me", "MSG-1", attachment_root / "doc.txt", False, None)

        assert [request.method for request in recorded] == ["POST", "PUT"]
