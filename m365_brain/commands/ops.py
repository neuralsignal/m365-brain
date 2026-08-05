"""`ops resolve-links`, `ops tiers`, `ops triage` -- the three operational reports.

Option parsing, one library call, formatting. Every threshold these verbs apply
lives in `ops:`, so there is no `--min-per-month` and no `--lookback`: a flag
that overrode a configured threshold would mean the report a person ran by hand
and the report a scheduler ran disagreed, with nothing in either output saying
which threshold produced it.

`ops triage`'s field options are the one exception, and they are overrides of
`ops.triage.fields` rather than a way to supply it: a category name is a
statement about what the corpus contains, not a threshold, so pointing the same
rule at a second corpus for one run says nothing about the first. Omitting them
all is the ordinary case. The absent value is `None` and means "read the config"
-- there is no default category name here, because a guessed frontmatter
vocabulary produces an empty report that looks like an empty inbox.
"""

from __future__ import annotations

from datetime import UTC, datetime

import click

from m365_brain.commands._context import emit, open_workspace, require_config
from m365_brain.config import Config, require_section
from m365_brain.config.ops import OpsConfig, TriageFieldsConfig
from m365_brain.ops.links import resolve_links
from m365_brain.ops.tiers import compute_tiers
from m365_brain.ops.triage import triage
from m365_brain.outbox.filesystem_store import FilesystemIntentStore
from m365_brain.storage import create_storage
from m365_brain.vault.paths import VaultPaths


@click.group("ops")
def ops_group() -> None:
    """Link resolution, relationship tiers and inbox triage."""


def _sections(ctx: click.Context) -> tuple[Config, OpsConfig, int]:
    """The config, its `ops:` section, and the page size index reads use."""
    config = require_config(ctx)
    return config, require_section(config.ops, "ops"), require_section(config.index, "index").search.page_size


@ops_group.command("resolve-links")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON on stdout")
@click.pass_context
def resolve_links_command(ctx: click.Context, as_json: bool) -> None:
    """Unresolved wikilinks, and the entities they appear to mean."""
    config, ops, page_size = _sections(ctx)
    with open_workspace(config) as workspace:
        resolutions = resolve_links(workspace.backend, ops.link_resolution, page_size)

    payload = {
        "resolutions": [
            {
                "source": resolution.source.permalink,
                "link": resolution.link_text,
                "matched": None if resolution.matched is None else resolution.matched.permalink,
                "matched_title": None if resolution.matched is None else resolution.matched.title,
                "confidence": resolution.confidence,
            }
            for resolution in resolutions
        ]
    }
    emit(
        as_json,
        payload,
        [
            f"{row['confidence']}\t{row['link']}\t{row['matched'] or '-'}\t{row['source']}"
            for row in payload["resolutions"]
        ],
    )


@ops_group.command("tiers")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON on stdout")
@click.pass_context
def tiers_command(ctx: click.Context, as_json: bool) -> None:
    """Relationship tiers, computed from interaction counts over the lookback window."""
    config, ops, page_size = _sections(ctx)
    with open_workspace(config) as workspace:
        assignments = compute_tiers(workspace.backend, ops.tiers, datetime.now(UTC), page_size)

    payload = {
        "assignments": [
            {
                "party": assignment.party,
                "tier": assignment.tier,
                "interactions": assignment.interactions,
                "per_month": assignment.per_month,
                "last_interaction": assignment.last_interaction.isoformat(),
                "stale": assignment.stale,
            }
            for assignment in assignments
        ]
    }
    emit(
        as_json,
        payload,
        [
            f"{row['tier']}\t{row['interactions']}\t{'stale' if row['stale'] else 'fresh'}\t{row['party']}"
            for row in payload["assignments"]
        ],
    )


def _fields(configured: TriageFieldsConfig, overrides: dict[str, str | None]) -> TriageFieldsConfig:
    """`ops.triage.fields`, with any option the caller passed substituted in.

    Rebuilt through the model rather than `model_copy`, so an override goes
    through the same validation the config file does -- `model_copy` skips it,
    and a config-shaped object that the loader would have rejected is exactly
    what should never reach the library.
    """
    stated = {name: value for name, value in overrides.items() if value is not None}
    return TriageFieldsConfig.model_validate(configured.model_dump() | stated)


@ops_group.command("triage")
@click.option("--timeframe", type=str, required=True, help="e.g. 7d, 2 weeks ago")
@click.option("--entity-type", type=str, default=None, help="Override ops.triage.fields.entity_type")
@click.option("--folder-category", type=str, default=None, help="Override ops.triage.fields.folder")
@click.option("--conversation-category", type=str, default=None, help="Override ops.triage.fields.conversation_id")
@click.option("--message-id-category", type=str, default=None, help="Override ops.triage.fields.message_id")
@click.option("--sender-category", type=str, default=None, help="Override ops.triage.fields.sender")
@click.option("--recipients-category", type=str, default=None, help="Override ops.triage.fields.recipients")
@click.option("--timestamp-category", type=str, default=None, help="Override ops.triage.fields.timestamp")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON on stdout")
@click.pass_context
def triage_command(
    ctx: click.Context,
    timeframe: str,
    entity_type: str | None,
    folder_category: str | None,
    conversation_category: str | None,
    message_id_category: str | None,
    sender_category: str | None,
    recipients_category: str | None,
    timestamp_category: str | None,
    as_json: bool,
) -> None:
    """Received messages with no reply and no recorded rejection."""
    config, ops, page_size = _sections(ctx)
    outboxes = require_section(config.outboxes, "outboxes")
    vault = require_section(config.vault, "vault")
    store = FilesystemIntentStore(
        create_storage(config.storage), VaultPaths(vault), tuple(sorted(outboxes.definitions))
    )
    fields = _fields(
        ops.triage.fields,
        {
            "entity_type": entity_type,
            "folder": folder_category,
            "conversation_id": conversation_category,
            "message_id": message_id_category,
            "sender": sender_category,
            "recipients": recipients_category,
            "timestamp": timestamp_category,
        },
    )

    with open_workspace(config) as workspace:
        items = triage(workspace.backend, store, ops.triage, fields, timeframe, datetime.now(UTC), page_size)

    payload = {
        "messages": [
            {
                "permalink": item.entity.permalink,
                "subject": item.subject,
                "sender": item.sender,
                "received_at": item.received_at.isoformat(),
                "conversation_id": item.conversation_id,
                "is_forward": item.is_forward,
                "is_cc_only": item.is_cc_only,
            }
            for item in items
        ]
    }
    emit(
        as_json,
        payload,
        [f"{row['received_at']}\t{row['sender'] or '-'}\t{row['subject']}" for row in payload["messages"]],
    )
