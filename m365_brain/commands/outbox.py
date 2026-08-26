"""`outbox list`, `outbox push`, `outbox reconcile`.

There is no `outbox new`. An intent *is* a markdown file in the outbox
directory -- that is the interface, and the reason the outbox is file-based. A
verb that wrote the same bytes would be a second way to do one thing.
`teams post` is the single exception and lives in its own module, because a
channel intent needs a `(team_id, channel_id)` pair no human types from memory.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

import click

from m365_brain.commands._context import EXIT_FAILURE, emit, require_config
from m365_brain.config import Config, ConfigError, require_section
from m365_brain.m365.auth.profiles import AuthProfiles
from m365_brain.m365.client import GraphClient
from m365_brain.m365.errors import GraphNotFoundError
from m365_brain.m365.outboxes import build_handlers
from m365_brain.outbox.authority import AuthorityRouter
from m365_brain.outbox.filesystem_store import FilesystemIntentStore
from m365_brain.outbox.reconcile import RECONCILE_SELECT, QuoteMarkers
from m365_brain.outbox.registry import build_registry
from m365_brain.outbox.runner import push as push_pass
from m365_brain.outbox.runner import reconcile as reconcile_pass
from m365_brain.storage import create_storage
from m365_brain.vault.paths import VaultPaths


@click.group("outbox")
def outbox_group() -> None:
    """Inspect, dispatch and reconcile write-back intents."""


def _store(config: Config, names: tuple[str, ...]) -> FilesystemIntentStore:
    vault = require_section(config.vault, "vault")
    return FilesystemIntentStore(create_storage(config.storage), VaultPaths(vault), names)


def _names(config: Config, only: str | None) -> tuple[str, ...]:
    outboxes = require_section(config.outboxes, "outboxes")
    configured = tuple(sorted(outboxes.definitions))
    if only is None:
        return configured
    if only not in configured:
        raise ConfigError(f"no outbox named {only!r}; configured: {list(configured)}")
    return (only,)


@contextmanager
def _clients(config: Config) -> Iterator[dict[str, GraphClient]]:
    """One Graph client per auth profile an outbox names, closed on exit."""
    outboxes = require_section(config.outboxes, "outboxes")
    profiles = AuthProfiles(config.auth.profiles or {}, config.graph.timeout_seconds)
    wanted = sorted({definition.auth_profile for definition in outboxes.definitions.values()})
    opened: dict[str, GraphClient] = {}
    try:
        for name in wanted:
            opened[name] = GraphClient(config.graph, profiles.provider(name))
        yield opened
    finally:
        for client in opened.values():
            client.close()


@outbox_group.command("list")
@click.option("--outbox", "only", type=str, default=None, help="One outbox; absent means all")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON on stdout")
@click.pass_context
def list_intents(ctx: click.Context, only: str | None, as_json: bool) -> None:
    """Pending, in-flight and archived intents, with their status."""
    config = require_config(ctx)
    outboxes = require_section(config.outboxes, "outboxes")
    names = _names(config, only)
    store = _store(config, tuple(sorted(outboxes.definitions)))

    rows = [
        {"uuid": uuid, "outbox": name, "authority": outboxes.definitions[name].authority, "status": "pending"}
        for name, uuid in store.pending()
        if name in names
    ]
    rows += [{"uuid": uuid, "outbox": None, "authority": None, "status": "inflight"} for uuid in store.inflight()]
    rows += [
        {
            "uuid": receipt.uuid,
            "outbox": receipt.kind,
            "authority": None,
            "status": receipt.outcome,
            "graph_message_id": receipt.graph_message_id,
        }
        for receipt in store.dispatched_receipts()
    ]
    emit(as_json, {"intents": rows}, [f"{row['status']:<10} {row['uuid']} {row['outbox'] or '-'}" for row in rows])


@outbox_group.command("push")
@click.option("--outbox", "only", type=str, default=None, help="One outbox; absent means all")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON on stdout")
@click.pass_context
def push(ctx: click.Context, only: str | None, as_json: bool) -> None:
    """Claim, route, dispatch, receipt and archive every pending intent."""
    config = require_config(ctx)
    outboxes = require_section(config.outboxes, "outboxes")
    m365 = require_section(config.m365, "m365")
    names = _names(config, only)
    store = _store(config, names)

    with _clients(config) as clients:
        registry = build_registry(
            config.outboxes, config.auth.profiles or {}, build_handlers(outboxes, m365.upload, clients)
        )
        counts = push_pass(store, registry, AuthorityRouter())

    payload = counts.as_dict()
    emit(as_json, payload, [f"{key}={value}" for key, value in sorted(payload.items())])
    if counts.failed:
        raise SystemExit(EXIT_FAILURE)


@outbox_group.command("reconcile")
@click.option("--outbox", "only", type=str, default=None, help="One outbox; absent means all")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON on stdout")
@click.pass_context
def reconcile(ctx: click.Context, only: str | None, as_json: bool) -> None:
    """Ask Graph what became of every dispatched draft."""
    config = require_config(ctx)
    outboxes = require_section(config.outboxes, "outboxes")
    store = _store(config, _names(config, only))
    markers = QuoteMarkers.from_config(outboxes.reconcile.quote_markers)

    with _clients(config) as clients:
        client = next(iter(clients.values()))

        def fetch(mailbox: str, message_id: str, select: list[str]) -> dict | None:
            """Translate "the draft is gone" into the `None` `classify` reads.

            The adapter owes the outbox core this: `reconcile()` takes a
            callable so it never imports a Graph error type, so a 404 that is
            not converted here escapes the pass entirely -- and because the
            receipt is marked only *after* the fetch, the same receipt poisons
            every subsequent run. Narrowly `GraphNotFoundError`: `None` becomes
            the terminal verdict `rejected`, and widening this to
            `GraphApiError` would file a permanent "the user deleted it" every
            time Graph returned a 500.
            """
            base = "/me" if mailbox == "me" else f"/users/{mailbox}"
            try:
                return client.get(
                    f"{base}/messages/{message_id}", params={"$select": ",".join(select or RECONCILE_SELECT)}
                )
            except GraphNotFoundError:
                return None

        outcomes = reconcile_pass(store, fetch, markers)

    payload = {"outcomes": [outcome.model_dump(mode="json") for outcome in outcomes]}
    emit(as_json, payload, [f"{outcome.verdict:<10} {outcome.uuid}" for outcome in outcomes])
