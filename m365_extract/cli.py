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
import time
from datetime import UTC, datetime
from pathlib import Path

import click
import structlog
from dotenv import find_dotenv, load_dotenv

from m365_extract.auth.device_code import DeviceCodeAuth
from m365_extract.auth.token_provider import make_cli_token_provider
from m365_extract.config import load_config
from m365_extract.continuous import run_continuous
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
@click.option("--continuous", is_flag=True, help="Run extractors on their configured intervals")
@click.option(
    "--dry-run", "dry_run_flag", is_flag=True, help="Validate auth and probe each extractor without writing files"
)
@click.option("--extractors", "extractor_names", type=str, help="Comma-separated list of extractors to run")
@click.pass_context
def sync(ctx: click.Context, once: bool, continuous: bool, dry_run_flag: bool, extractor_names: str | None) -> None:
    """Sync Microsoft 365 data."""
    if not once and not continuous and not dry_run_flag:
        raise click.UsageError("Specify --once, --continuous, or --dry-run")

    config = load_config(ctx.obj["config_path"])

    configure_logging(config.service.log_level, config.service.json_logs)

    token_provider = make_cli_token_provider(config.auth)

    # Determine which extractors to run
    if extractor_names:
        # Explicit --extractors flag: trust the user's choice, no config filtering
        names = [n.strip() for n in extractor_names.split(",")]
    else:
        # No flag: run only extractors enabled in config
        names = [name for name, (_, cfg_getter, _) in EXTRACTORS.items() if cfg_getter(config).enabled]

    if dry_run_flag:
        dry_run(config, token_provider, names)
        return

    storage = create_storage(config.storage)
    sync_state = SyncState(config.state.state_file_path)

    if once:
        run_extractors(config, token_provider, storage, sync_state, names)
    elif continuous:
        run_continuous(config, token_provider, storage, sync_state, names)


@main.command()
@click.option("--poll-interval", "poll_interval", type=int, help="Seconds between daemon cycles (overrides config)")
@click.pass_context
def daemon(ctx: click.Context, poll_interval: int | None) -> None:
    """Run the multi-user daemon: sync all enabled users from the database."""
    # Deferred imports — daemon mode requires sqlmodel + admin deps
    from alembic.config import Config as AlembicConfig
    from sqlmodel import create_engine

    from alembic import command as alembic_command
    from m365_extract.daemon import run_daemon_cycle, write_health_file

    config = load_config(ctx.obj["config_path"])
    configure_logging(config.service.log_level, config.service.json_logs)

    if config.web is None:
        raise click.UsageError("daemon mode requires a config file with a 'web:' section (e.g., config.web.yaml)")

    engine = create_engine(config.web.db_url)

    # Run Alembic migrations to HEAD instead of create_all()
    alembic_ini = str(Path(__file__).resolve().parent.parent / "alembic.ini")
    alembic_cfg = AlembicConfig(alembic_ini)
    alembic_cfg.set_main_option("sqlalchemy.url", config.web.db_url)
    alembic_command.upgrade(alembic_cfg, "head")
    log.info("daemon.migrations_applied")

    from m365_admin.services.token_service import TokenService, TokenServiceAdapter

    token_service = TokenService(fernet_key=config.web.fernet_key)
    token_adapter = TokenServiceAdapter(token_service=token_service, engine=engine)

    first_config = ctx.obj["config_path"].split(",")[0].strip()
    state_dir = str(Path(first_config).resolve().parent / "state")
    interval = poll_interval if poll_interval is not None else config.service.continuous_poll_seconds

    log.info("daemon.started", poll_interval=interval, state_dir=state_dir)

    try:
        while True:
            run_daemon_cycle(config, engine, token_adapter, state_dir)
            write_health_file(state_dir)
            time.sleep(interval)
    except KeyboardInterrupt:
        log.info("daemon.stopped")
