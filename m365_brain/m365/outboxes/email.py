"""The email executor: one handler class, three registered instances.

`email.draft`, `email.reply` and `email.forward` differ in which Graph call
opens the draft and in nothing else -- same attachments, same signature rules,
same mailbox dispatch -- so they are one class checked against its registered
name rather than three near-copies that drift.

Everything is resolved before the first Graph call. A missing attachment
discovered after the draft exists leaves a half-assembled message in somebody's
mailbox, and "the draft is there but incomplete" is a worse failure than "no
draft, here is the path that was missing".
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from m365_brain.config import EmailSignatureConfig, UploadConfig
from m365_brain.m365.client import GraphClient
from m365_brain.m365.outboxes.attachments import MessageTarget, attach_file, resolve_attachment
from m365_brain.m365.outboxes.messages import (
    FORWARD,
    REPLY,
    REPLY_ALL,
    create_new_draft,
    create_reply_like,
    get_message,
    mailbox_base,
    update_draft,
)
from m365_brain.m365.outboxes.rendering import markdown_to_outlook_html
from m365_brain.vault.dispatch import DRAFT_ONLY_OPS, DispatchResult, GraphOp
from m365_brain.vault.intent import IntentEnvelope
from m365_brain.vault.payloads import _EmailCommon

DRAFT_KIND = "email.draft"
REPLY_KIND = "email.reply"
FORWARD_KIND = "email.forward"

LIVENESS_SELECT = ["id", "isDraft"]


class EmailIntentError(Exception):
    """The intent asks for something this outbox cannot do correctly."""


@dataclass(frozen=True)
class _Assets:
    """Everything resolved from disk before any request is made."""

    attachments: tuple[Path, ...]
    inline_images: tuple[tuple[str, Path], ...]
    signature_html: str
    logo: Path | None


@dataclass(frozen=True)
class EmailOutbox:
    """Creates and revises Outlook drafts. Never sends: see `declared_ops`."""

    name: str
    client: GraphClient
    upload: UploadConfig
    attachment_root: str
    signature: EmailSignatureConfig
    signature_html: str
    """Read once at construction. A signature file that has gone missing is a
    startup failure, not a surprise on the first draft of the day."""

    declared_ops: frozenset[GraphOp] = DRAFT_ONLY_OPS

    def execute(self, envelope: IntentEnvelope) -> DispatchResult:
        payload = envelope.payload
        if payload.kind != self.name:
            raise EmailIntentError(
                f"outbox {self.name!r} received a {payload.kind!r} payload; "
                "the registered name and the payload kind are one identity"
            )
        assets = self._resolve(payload)
        if payload.revises_message_id is None:
            message_id = self._create(payload, assets)
        else:
            message_id = self._revise(payload, assets)
        return DispatchResult(graph_message_id=message_id)

    def _resolve(self, payload: _EmailCommon) -> _Assets:
        signature_html = self.signature_html if payload.include_signature else ""
        # The logo is suppressed with the signature: without the signature HTML
        # there is no `cid:` reference for it to resolve, so attaching it would
        # add an unreferenced image to every draft.
        logo: Path | None = None
        if signature_html and self.signature.logo_path is not None:
            logo = resolve_attachment(self.attachment_root, self.signature.logo_path)
        return _Assets(
            attachments=tuple(
                resolve_attachment(self.attachment_root, item.path) for item in (payload.attachments or [])
            ),
            inline_images=tuple(
                (image.cid, resolve_attachment(self.attachment_root, image.path))
                for image in (payload.inline_images or [])
            ),
            signature_html=signature_html,
            logo=logo,
        )

    def _create(self, payload: _EmailCommon, assets: _Assets) -> str:
        body_html = markdown_to_outlook_html(payload.body)
        if payload.kind == DRAFT_KIND:
            message_id = create_new_draft(
                self.client,
                payload.mailbox,
                list(payload.to),
                list(payload.cc or []),
                list(payload.bcc or []),
                payload.subject,
                body_html,
                assets.signature_html,
            )
        else:
            message_id = create_reply_like(
                self.client,
                payload.mailbox,
                payload.in_reply_to,
                self._action(payload),
                body_html,
                assets.signature_html,
                list(payload.cc or []),
                list(payload.to) if payload.kind == FORWARD_KIND else None,
            )
        try:
            self._attach(payload.mailbox, message_id, assets)
        except Exception as exc:
            # Point of no return. The draft is in the mailbox; a retry would
            # create a second one. `outbox/runner.py` reads `transient` off the
            # *instance*, so clearing it here downgrades a would-be retry to a
            # terminal failure without changing the exception's type -- which
            # `_classify_failure` still reads for `attachment_missing` and
            # `etag_conflict`.
            exc.transient = False  # type: ignore[attr-defined]
            raise
        return message_id

    def _revise(self, payload: _EmailCommon, assets: _Assets) -> str:
        """Refresh a draft in place, or recreate it when the target is gone.

        A revision of a reply or forward is refused rather than approximated.
        The stored body is `user text + signature + Graph's quoted original`
        and the three are indistinguishable once merged, so a PATCH would
        either drop the quote or duplicate it. The implementation this replaces
        dropped it, silently.
        """
        if payload.kind != DRAFT_KIND:
            raise EmailIntentError(
                f"revises_message_id is not supported for {payload.kind!r}: patching the body would "
                "drop the quoted original Graph generated. Delete the draft and issue a new intent."
            )
        live = get_message(self.client, payload.mailbox, payload.revises_message_id, LIVENESS_SELECT)
        if live is None or live.get("isDraft") is False:
            # Deleted, or already sent. Both mean the id points at nothing
            # revisable, and neither is an error -- create a fresh draft.
            return self._create(payload, assets)
        return update_draft(
            self.client,
            payload.mailbox,
            payload.revises_message_id,
            list(payload.to),
            list(payload.cc or []),
            list(payload.bcc or []),
            payload.subject,
            markdown_to_outlook_html(payload.body),
            assets.signature_html,
        )

    def _action(self, payload: _EmailCommon) -> str:
        if payload.kind == FORWARD_KIND:
            return FORWARD
        return REPLY_ALL if payload.reply_all else REPLY

    def _attach(self, mailbox: str, message_id: str, assets: _Assets) -> None:
        """User files, then the signature logo, then inline images -- the order
        the working sender used, and therefore the order the parity fixtures
        recorded."""
        target = MessageTarget(mailbox_base(mailbox), message_id)
        for path in assets.attachments:
            attach_file(self.client, self.upload, target, path, False, None)
        if assets.logo is not None:
            attach_file(self.client, self.upload, target, assets.logo, True, self.signature.logo_content_id)
        for cid, path in assets.inline_images:
            attach_file(self.client, self.upload, target, path, True, cid)


def load_signature_html(signature: EmailSignatureConfig) -> str:
    """Read the configured signature HTML. `null` means "no signature".

    Missing-file is a crash rather than an empty string: an operator who
    pointed at the wrong path should learn at startup, not by noticing that a
    week of drafts went out unsigned.
    """
    if signature.html_path is None:
        return ""
    path = Path(signature.html_path)
    if not path.exists():
        raise FileNotFoundError(
            f"outboxes.email.signature.html_path points at {path}, which does not exist. "
            "Set it to null if this deployment has no signature."
        )
    return path.read_text(encoding="utf-8")
