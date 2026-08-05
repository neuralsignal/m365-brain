"""The intent file: YAML frontmatter envelope, markdown body, typed payload.

Markdown rather than JSON, for two reasons the port has to keep. An agent
authoring an email writes prose, and prose escaped into a JSON string is
unreviewable. And the file is diffable and indexable by the same machinery that
reads every other file in the vault.

So the frontmatter *is* the envelope and the markdown body *is* the payload's
`body` field -- one rule, applied to every payload kind, with no per-kind
indirection. A frontmatter `body:` key is therefore a hard parse error: the
implementation this replaces did `metadata["body"] = post.content`, which
silently clobbered whatever the author had written under that key.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import frontmatter
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from m365_brain.vault.payloads import IntentPayload

BODY_KEY = "body"
PAYLOAD_KEY = "payload"


class IntentParseError(Exception):
    """A file under the outbox is not a valid intent. Always names the source."""


class IntentEnvelope(BaseModel):
    """Who asked for what, and under which idempotency key."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    uuid: str = Field(min_length=1, max_length=64)
    """Client-supplied, never generated here. The authoring agent picks it and
    the archive keys replay detection off it, so a library that minted its own
    would break idempotency for the one caller that needs it most: a retry."""

    schema_version: int
    """An intent outlives the code that wrote it -- the archive is a ledger, and
    a ledger with no version on its rows cannot be read twice."""

    created_at: datetime
    created_by: str = Field(min_length=1, max_length=128)
    payload: IntentPayload

    @property
    def kind(self) -> str:
        """The payload's discriminator. There is no separate `outbox` field:
        two values that must agree is the defect DRY names, and the port it
        came from needed an explicit gate purely to police that duplication."""
        return str(self.payload.kind)


def parse_intent(content: str, source_ref: str, expected_uuid: str) -> IntentEnvelope:
    """Parse one intent. Every failure raises `IntentParseError` naming `source_ref`.

    `expected_uuid` is the filename stem. It must equal `envelope.uuid`: the
    implementation this replaces never cross-checked them, and consequently its
    database row, its blob path and its processed-archive name used one value
    while its rejected-archive name used the other -- three code paths
    disagreeing about which item they were talking about.
    """
    try:
        post = frontmatter.loads(content)
    except Exception as exc:  # noqa: BLE001 -- any YAML failure is one parse error
        raise IntentParseError(f"{source_ref}: frontmatter did not parse: {exc}") from exc

    metadata = dict(post.metadata)
    if BODY_KEY in metadata:
        raise IntentParseError(
            f"{source_ref}: frontmatter carries a {BODY_KEY!r} key. The markdown body is the "
            "payload body; a frontmatter key of that name would be silently overwritten."
        )
    payload = metadata.get(PAYLOAD_KEY)
    if not isinstance(payload, dict):
        raise IntentParseError(f"{source_ref}: frontmatter needs a {PAYLOAD_KEY!r} mapping")
    if BODY_KEY in payload:
        raise IntentParseError(
            f"{source_ref}: {PAYLOAD_KEY}.{BODY_KEY} is set in frontmatter. It comes from the "
            "markdown body and nowhere else."
        )
    metadata[PAYLOAD_KEY] = {**payload, BODY_KEY: post.content}

    try:
        envelope = IntentEnvelope.model_validate(metadata)
    except ValidationError as exc:
        raise IntentParseError(f"{source_ref}: {exc}") from exc

    if envelope.uuid != expected_uuid:
        raise IntentParseError(
            f"{source_ref}: envelope uuid {envelope.uuid!r} does not match the filename stem "
            f"{expected_uuid!r}. The two are one identity, so a mismatch is unresolvable."
        )
    return envelope


def parse_intent_file(path: Path) -> IntentEnvelope:
    """Parse an intent from disk, taking the expected uuid from the filename stem."""
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise IntentParseError(f"{path}: unreadable: {exc}") from exc
    return parse_intent(content, str(path), path.stem)


def dump_intent(envelope: IntentEnvelope) -> str:
    """Serialise an envelope back to markdown-with-frontmatter.

    Round-trips: `parse_intent(dump_intent(e), ref, e.uuid) == e`. That matters
    because the archived intent is the fixture every later comparison runs
    against -- reconciliation diffs the sent message against this body.
    """
    # `mode="python"` deliberately: `created_at` stays a datetime, which YAML
    # writes as a native timestamp and reads back as one. Dumping to an ISO
    # *string* would round-trip through YAML as a string and fail the
    # envelope's own `strict=True` -- an archive that cannot re-read itself.
    data = envelope.model_dump()
    payload = dict(data.pop(PAYLOAD_KEY))
    body = payload.pop(BODY_KEY)
    data[PAYLOAD_KEY] = payload
    return frontmatter.dumps(frontmatter.Post(body, **data))
