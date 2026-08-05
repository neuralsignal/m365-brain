"""`teams post` -- write a channel-post intent, resolving the ids from a URL.

The one authoring verb in the CLI, and it earns the exception: a Teams intent
needs a `(team_id, channel_id)` pair that nobody types from memory, and both
are sitting inside the "Get link to channel" URL that Teams puts on the
clipboard. Resolving them is real work; writing the file afterwards is not.

It does **not** send. `outbox push` remains the only execution path, so there
is still exactly one.
"""

from __future__ import annotations

import uuid as uuid_module
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import click
import yaml

from m365_brain.commands._context import emit, require_config
from m365_brain.config import ConfigError, require_section
from m365_brain.storage import create_storage
from m365_brain.vault.paths import VaultPaths

CHANNEL_OUTBOX = "teams.post_message"
"""The outbox a channel post is written to. Fixed rather than configurable:
it is the payload kind's own name -- `TeamsPostPayload.kind` -- and the registry
validates it exists. Two spellings of one name is how the shipped template came
to configure an outbox no executor implemented."""

SCHEMA_VERSION = 1


@click.group("teams")
def teams_group() -> None:
    """Author Teams intents. Dispatch is `outbox push`."""


def parse_channel_url(url: str) -> tuple[str, str]:
    """`(team_id, channel_id)` out of a "Get link to channel" URL.

    Raises naming both halves rather than returning a partial pair: an intent
    with one correct id posts nowhere, slowly.
    """
    parsed = urlparse(url)
    segments = [unquote(part) for part in parsed.path.split("/") if part]
    channel_id = next((part for part in segments if part.startswith("19:")), None)
    team_id = parse_qs(parsed.query).get("groupId", [None])[0]
    if channel_id is None or team_id is None:
        raise ConfigError(
            f"cannot read a team id and a channel id out of {url!r}. "
            "Use the 'Get link to channel' URL, which carries the channel id in its path "
            "and ?groupId= in its query."
        )
    return team_id, channel_id


@teams_group.command("post")
@click.option("--channel-url", required=True, type=str, help="A 'Get link to channel' URL")
@click.option("--body-file", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--created-by", required=True, type=str, help="Who is asking; recorded on the intent")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON on stdout")
@click.pass_context
def post(ctx: click.Context, channel_url: str, body_file: Path, created_by: str, as_json: bool) -> None:
    """Write a channel-post intent to the outbox. Sends nothing."""
    config = require_config(ctx)
    outboxes = require_section(config.outboxes, "outboxes")
    if CHANNEL_OUTBOX not in outboxes.definitions:
        raise ConfigError(
            f"outboxes.definitions has no {CHANNEL_OUTBOX!r} entry, so a channel post has nowhere to go "
            f"(configured: {sorted(outboxes.definitions)})"
        )
    team_id, channel_id = parse_channel_url(channel_url)
    vault = require_section(config.vault, "vault")
    intent_uuid = str(uuid_module.uuid4())

    envelope = {
        "uuid": intent_uuid,
        "schema_version": SCHEMA_VERSION,
        "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "created_by": created_by,
        "payload": {"kind": "teams.post_message", "team_id": team_id, "channel_id": channel_id},
    }
    document = f"---\n{yaml.safe_dump(envelope, sort_keys=True)}---\n{body_file.read_text(encoding='utf-8')}"
    path = VaultPaths(vault).outbox_intent(CHANNEL_OUTBOX, intent_uuid)
    create_storage(config.storage).write_file(path, document)

    emit(as_json, {"path": path, "uuid": intent_uuid, "team_id": team_id, "channel_id": channel_id}, [path])
