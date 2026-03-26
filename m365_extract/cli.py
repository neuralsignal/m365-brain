"""CLI entry point for m365-extract.

Commands:
  m365-extract auth login       — Authenticate via device code flow
  m365-extract auth status      — Show cached token info
  m365-extract sync --once      — Run all enabled extractors once
  m365-extract sync --dry-run   — Validate auth + scopes without writing files
  m365-extract worker           — Run the sync worker (multi-user, per-extractor jobs)
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import click
import structlog
from dotenv import find_dotenv, load_dotenv

from m365_extract.auth.device_code import DeviceCodeAuth
from m365_extract.auth.token_provider import make_cli_token_provider
from m365_extract.config import load_config
from m365_extract.dry_run import dry_run
from m365_extract.logging_config import configure_logging
from m365_extract.state import SyncState
from m365_extract.storage import create_storage
from m365_extract.sync import EXTRACTORS, run_extractors

log = structlog.get_logger()


@click.group()
@click.option("--config", "config_path", required=True, type=str, help="Comma-separated config YAML paths")
@click.pass_context
def main(ctx: click.Context, config_path: str) -> None:
    """m365-extract: Sync Microsoft 365 data to Obsidian-compatible markdown."""
    # Load .env from first config file's directory, then walk up from CWD as fallback
    first_path = config_path.split(",")[0].strip()
    config_dir_env = Path(first_path).resolve().parent / ".env"
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
@click.option("--once", is_flag=True, help="Run all enabled extractors once and exit")
@click.option(
    "--dry-run", "dry_run_flag", is_flag=True, help="Validate auth and probe each extractor without writing files"
)
@click.option("--extractors", "extractor_names", type=str, help="Comma-separated list of extractors to run")
@click.pass_context
def sync(ctx: click.Context, once: bool, dry_run_flag: bool, extractor_names: str | None) -> None:
    """Sync Microsoft 365 data (single user, CLI mode)."""
    if not once and not dry_run_flag:
        raise click.UsageError("Specify --once or --dry-run")

    config = load_config(ctx.obj["config_path"])

    configure_logging(config.service.log_level, config.service.json_logs)

    token_provider = make_cli_token_provider(config.auth)

    if extractor_names:
        names = [n.strip() for n in extractor_names.split(",")]
    else:
        names = [name for name, (_, cfg_getter, _) in EXTRACTORS.items() if cfg_getter(config).enabled]

    if dry_run_flag:
        dry_run(config, token_provider, names)
        return

    storage = create_storage(config.storage)
    sync_state = SyncState(config.state.state_file_path)

    run_extractors(config, token_provider, storage, sync_state, names)


@main.command()
@click.pass_context
def worker(ctx: click.Context) -> None:
    """Run the sync worker — processes (user, extractor) jobs from the database."""
    from sqlmodel import create_engine

    from m365_admin.services.token_service import TokenService, TokenServiceAdapter
    from m365_extract.worker import worker_loop

    config = load_config(ctx.obj["config_path"])
    configure_logging(config.service.log_level, config.service.json_logs)

    if config.web is None:
        raise click.UsageError("worker requires a config with a 'web' section (db_url, fernet_key)")

    engine = create_engine(config.web.db_url)

    token_service = TokenService(fernet_key=config.web.fernet_key)
    token_adapter = TokenServiceAdapter(token_service=token_service, engine=engine)

    config_path = ctx.obj["config_path"]
    first_config = config_path.split(",")[0].strip()
    state_dir = str(Path(first_config).resolve().parent / "state")

    worker_loop(config, engine, token_adapter, state_dir)
