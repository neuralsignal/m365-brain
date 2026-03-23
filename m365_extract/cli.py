"""CLI entry point for m365-extract.

Commands:
  m365-extract auth login       — Authenticate via device code flow
  m365-extract auth status      — Show cached token info
  m365-extract sync --once      — Run all enabled extractors once
  m365-extract sync --continuous — Run extractors on their configured intervals
  m365-extract sync --dry-run   — Validate auth + scopes without writing files
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import click
import structlog
from dotenv import find_dotenv, load_dotenv

from m365_extract.auth.device_code import DeviceCodeAuth
from m365_extract.auth.token_provider import make_cli_token_provider
from m365_extract.config import Config, load_config
from m365_extract.graph_client import GraphClient
from m365_extract.state import SyncState
from m365_extract.storage import create_storage
from m365_extract.storage.base import StorageBackend
from m365_extract.sync import EXTRACTORS, run_extractors

log = structlog.get_logger()


@click.group()
@click.option("--config", "config_path", required=True, type=click.Path(exists=True), help="Path to config.yaml")
@click.pass_context
def main(ctx: click.Context, config_path: str) -> None:
    """m365-extract: Sync Microsoft 365 data to Obsidian-compatible markdown."""
    # Load .env from config file's directory first, then walk up from CWD as fallback
    config_dir_env = Path(config_path).resolve().parent / ".env"
    if config_dir_env.exists():
        load_dotenv(config_dir_env)
    load_dotenv(find_dotenv(usecwd=True))
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config_path


@main.group()
@click.pass_context
def auth(ctx: click.Context) -> None:
    """Authentication commands."""


@auth.command()
@click.pass_context
def login(ctx: click.Context) -> None:
    """Authenticate via device code flow (interactive)."""
    config = load_config(ctx.obj["config_path"])
    device_auth = DeviceCodeAuth(config.auth)
    token = device_auth.login()
    click.echo(f"Authenticated successfully. Token length: {len(token)}")


@auth.command()
@click.pass_context
def status(ctx: click.Context) -> None:
    """Show cached authentication status."""
    config = load_config(ctx.obj["config_path"])
    cache_path = Path(config.auth.token_cache_path)

    if not cache_path.exists():
        click.echo("No cached token found. Run: m365-extract --config config.yaml auth login")
        raise SystemExit(1)

    cache_data = json.loads(cache_path.read_text(encoding="utf-8"))

    accounts = cache_data.get("Account", {})
    if not accounts:
        click.echo("Token cache exists but contains no accounts. Run auth login to re-authenticate.")
        raise SystemExit(1)

    access_tokens = cache_data.get("AccessToken", {})

    click.echo("Cached authentication:")
    for _key, account in accounts.items():
        username = account.get("username", "unknown")
        realm = account.get("realm", "unknown")
        click.echo(f"  Account:  {username}")
        click.echo(f"  Tenant:   {realm}")

    if access_tokens:
        for _key, token_entry in access_tokens.items():
            target = token_entry.get("target", "")
            expires_on = token_entry.get("expires_on")
            if expires_on:
                exp_dt = datetime.fromtimestamp(int(expires_on), tz=UTC)
                now = datetime.now(tz=UTC)
                if exp_dt > now:
                    delta = exp_dt - now
                    minutes = int(delta.total_seconds() // 60)
                    click.echo(f"  Token:    valid (expires in {minutes}m)")
                else:
                    click.echo("  Token:    expired (will auto-refresh on next sync)")
            scopes = target.split() if target else []
            if scopes:
                click.echo(f"  Scopes:   {', '.join(sorted(scopes))}")
    else:
        click.echo("  Token:    no access tokens cached (will re-acquire on next sync)")


@main.command()
@click.pass_context
def serve(ctx: click.Context) -> None:
    """Start the web server (multi-user mode)."""
    import uvicorn

    from m365_extract.web.app import create_app

    config = load_config(ctx.obj["config_path"])
    app = create_app(config)
    uvicorn.run(app, host=config.web.host, port=config.web.port)


@main.command()
@click.option("--once", is_flag=True, help="Run all enabled extractors once and exit")
@click.option("--continuous", is_flag=True, help="Run extractors on their configured intervals")
@click.option("--dry-run", is_flag=True, help="Validate auth and probe each extractor without writing files")
@click.option("--extractors", "extractor_names", type=str, help="Comma-separated list of extractors to run")
@click.pass_context
def sync(ctx: click.Context, once: bool, continuous: bool, dry_run: bool, extractor_names: str | None) -> None:
    """Sync Microsoft 365 data."""
    if not once and not continuous and not dry_run:
        raise click.UsageError("Specify --once, --continuous, or --dry-run")

    config = load_config(ctx.obj["config_path"])

    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(config.service.log_level),
        ),
    )

    token_provider = make_cli_token_provider(config.auth)

    # Determine which extractors to run
    if extractor_names:
        names = [n.strip() for n in extractor_names.split(",")]
    else:
        names = list(EXTRACTORS.keys())

    if dry_run:
        _dry_run(config, token_provider, names)
        return

    storage = create_storage(config.storage)
    sync_state = SyncState(config.state.state_file_path)

    if once:
        run_extractors(config, token_provider, storage, sync_state, names)
    elif continuous:
        _run_continuous(config, token_provider, storage, sync_state, names)


# Maps extractor names to a lightweight Graph probe endpoint.
# Each returns a small payload to confirm the scope is granted.
_DRY_RUN_PROBES: dict[str, str] = {
    "email": "/me/mailFolders/Inbox/messages?$top=1&$select=id,subject",
    "calendar": "/me/calendarView?$top=1&$select=id,subject&startDateTime=2020-01-01T00:00:00Z&endDateTime=2099-12-31T00:00:00Z",
    "teams_chats": "/me/chats?$top=1&$select=id,topic",
    "teams_channels": "/me/joinedTeams?$top=1&$select=id,displayName",
    "onedrive": "/me/drive/root/children?$top=1&$select=id,name",
    "sharepoint": "/me/followedSites?$top=1&$select=id,displayName",
    "contacts": "/me/contacts?$top=1&$select=id,displayName",
    "directory": "/users?$top=1&$select=id,displayName",
}


def _dry_run(config: Config, token_provider: Callable[[], str], names: list[str]) -> None:
    """Validate auth and probe each extractor's endpoint without writing files."""
    from m365_extract.graph_client import GraphApiError

    click.echo("Dry run: validating authentication and extractor permissions...\n")

    # Step 1: Validate token by calling /me
    with GraphClient(config.graph, token_provider) as client:
        try:
            me = client.get("/me?$select=displayName,userPrincipalName")
            display_name = me.get("displayName", "unknown")
            upn = me.get("userPrincipalName", "unknown")
            click.echo(f"  Auth:     OK (signed in as {display_name} <{upn}>)")
        except GraphApiError as exc:
            click.echo(f"  Auth:     FAILED — {exc}")
            raise SystemExit(1) from exc

        # Step 2: Probe each enabled extractor
        passed = 0
        failed = 0
        for ext_name in names:
            if ext_name not in EXTRACTORS:
                click.echo(f"  {ext_name:16s} UNKNOWN (not a valid extractor)")
                failed += 1
                continue

            _, config_getter, _ = EXTRACTORS[ext_name]
            ext_config = config_getter(config)

            if not ext_config.enabled:
                click.echo(f"  {ext_name:16s} skipped (disabled)")
                continue

            probe_path = _DRY_RUN_PROBES.get(ext_name)
            if probe_path is None:
                click.echo(f"  {ext_name:16s} skipped (no probe configured)")
                continue

            try:
                data = client.get(probe_path)
                item_count = len(data.get("value", []))
                click.echo(f"  {ext_name:16s} OK ({item_count} item(s) in probe)")
                passed += 1
            except GraphApiError as exc:
                click.echo(f"  {ext_name:16s} FAILED — {exc}")
                failed += 1

    click.echo(f"\nDry run complete: {passed} passed, {failed} failed")
    if failed > 0:
        raise SystemExit(1)


def _run_continuous(
    config: Config, token_provider: Callable[[], str], storage: StorageBackend, sync_state: SyncState, names: list[str]
) -> None:
    """Run extractors continuously on their configured intervals."""
    click.echo("Running in continuous mode. Press Ctrl+C to stop.")

    # Track last run time per extractor
    last_run: dict[str, float] = {}

    try:
        while True:
            now = time.time()

            with GraphClient(config.graph, token_provider) as client:
                for ext_name in names:
                    if ext_name not in EXTRACTORS:
                        continue

                    module, config_getter, needs_converters = EXTRACTORS[ext_name]
                    ext_config = config_getter(config)

                    if not ext_config.enabled:
                        continue

                    interval_seconds = ext_config.poll_interval_minutes * 60
                    last = last_run.get(ext_name, 0)

                    if now - last < interval_seconds:
                        continue

                    log.info("cli.running_extractor", name=ext_name)
                    state = sync_state.load(ext_name)

                    try:
                        if needs_converters:
                            updated_state, count = module.run(client, storage, state, ext_config, config.converters)
                        else:
                            updated_state, count = module.run(client, storage, state, ext_config)
                        sync_state.save(ext_name, updated_state)
                        last_run[ext_name] = time.time()
                        log.info("cli.extractor_done", name=ext_name, items=count)
                    except Exception as exc:
                        log.error("cli.extractor_failed", name=ext_name, error=str(exc))
                        last_run[ext_name] = time.time()

            time.sleep(30)

    except KeyboardInterrupt:
        click.echo("\nStopped.")
