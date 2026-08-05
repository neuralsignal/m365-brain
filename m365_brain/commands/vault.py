"""`vault path` -- resolve one configured area to a path, and nothing else.

The verb exists so a script never has to know the layout. Every directory name
is config, and a caller that hardcodes `inbox/` is a caller that breaks the day
an operator renames it -- which is the whole reason `vault.layout` exists.
"""

from __future__ import annotations

import click

from m365_brain.commands._context import emit, require_config
from m365_brain.config import ConfigError, require_section
from m365_brain.vault.paths import VaultPaths, manifest_directory, state_directory

AREAS = ("inbox", "annotations", "outbox", "meta", "state", "manifests")


@click.group("vault")
def vault_group() -> None:
    """Resolve vault paths from config."""


@vault_group.command("path")
@click.argument("area", type=click.Choice(AREAS))
@click.option("--extractor", type=str, default=None, help="Required for `inbox`")
@click.option("--outbox", "outbox_name", type=str, default=None, help="Required for `outbox`")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON on stdout")
@click.pass_context
def path(ctx: click.Context, area: str, extractor: str | None, outbox_name: str | None, as_json: bool) -> None:
    """One absolute path for one area. Storage-relative areas gain the root."""
    config = require_config(ctx)
    vault = require_section(config.vault, "vault")
    resolver = VaultPaths(vault)

    if area == "inbox":
        if extractor is None:
            raise ConfigError("`vault path inbox` needs --extractor: the inbox is one directory per extractor")
        relative = resolver.inbox_root(extractor)
    elif area == "outbox":
        if outbox_name is None:
            raise ConfigError("`vault path outbox` needs --outbox: the outbox is one directory per outbox")
        _require_known_outbox(config, outbox_name)
        relative = resolver.outbox(outbox_name)
    elif area == "annotations":
        relative = resolver.annotations()
    elif area == "meta":
        relative = resolver.meta()
    elif area == "state":
        emit(as_json, {"path": str(state_directory(vault))}, [str(state_directory(vault))])
        return
    else:
        emit(as_json, {"path": str(manifest_directory(vault))}, [str(manifest_directory(vault))])
        return

    resolved = f"{vault.root.rstrip('/')}/{relative}"
    emit(as_json, {"path": resolved, "relative": relative}, [resolved])


def _require_known_outbox(config, name: str) -> None:
    outboxes = require_section(config.outboxes, "outboxes")
    if name not in outboxes.definitions:
        raise ConfigError(f"no outbox named {name!r}; configured: {sorted(outboxes.definitions)}")
