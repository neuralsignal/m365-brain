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
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import click
import structlog
from dotenv import find_dotenv, load_dotenv

from m365_extract.auth.device_code import DeviceCodeAuth
from m365_extract.auth.token_provider import make_cli_token_provider
from m365_extract.config import Config, load_config
from m365_extract.graph_client import GraphClient
from m365_extract.logging_config import configure_logging
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

    configure_logging(config.service.log_level, config.service.json_logs)

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
    # calendar probe computed dynamically — see _dry_run_probe_path()
    "teams_chats": "/me/chats?$top=1&$select=id,topic",
    "teams_channels": "/me/joinedTeams?$top=1&$select=id,displayName",
    "onedrive": "/me/drive/root/children?$top=1&$select=id,name",
    "sharepoint": "/me/followedSites?$top=1&$select=id,displayName",
    "contacts": "/me/contacts?$top=1&$select=id,displayName",
    "directory": "/users?$top=1&$select=id,displayName",
}


def _dry_run_probe_path(ext_name: str) -> str | None:
    """Return the Graph probe URL for a given extractor, or None.

    Calendar uses a dynamic ±30 day window because Graph rejects
    calendarView ranges exceeding 1825 days.
    """
    if ext_name == "calendar":
        now = datetime.now(tz=UTC)
        start = (now - timedelta(days=30)).strftime("%Y-%m-%dT00:00:00Z")
        end = (now + timedelta(days=30)).strftime("%Y-%m-%dT00:00:00Z")
        return f"/me/calendarView?$top=1&$select=id,subject&startDateTime={start}&endDateTime={end}"
    return _DRY_RUN_PROBES.get(ext_name)


def _dry_run(config: Config, token_provider: Callable[[], str], names: list[str]) -> None:
    """Validate auth and probe each extractor's endpoint without writing files."""
    from m365_extract.graph_client import GraphApiError

    log.info("cli.dry_run_start")

    # Step 1: Validate token by calling /me
    with GraphClient(config.graph, token_provider) as client:
        try:
            me = client.get("/me?$select=displayName,userPrincipalName")
            display_name = me.get("displayName", "unknown")
            upn = me.get("userPrincipalName", "unknown")
            log.info("cli.dry_run_auth_ok", user=display_name, upn=upn)
        except GraphApiError as exc:
            log.error("cli.dry_run_auth_failed", error=str(exc))
            raise SystemExit(1) from exc

        # Step 2: Probe each enabled extractor
        passed = 0
        failed = 0
        for ext_name in names:
            if ext_name not in EXTRACTORS:
                log.warning("cli.dry_run_probe_unknown", name=ext_name)
                failed += 1
                continue

            _, config_getter, _ = EXTRACTORS[ext_name]
            ext_config = config_getter(config)

            if not ext_config.enabled:
                log.info("cli.dry_run_probe_skipped", name=ext_name, reason="disabled")
                continue

            probe_path = _dry_run_probe_path(ext_name)
            if probe_path is None:
                log.info("cli.dry_run_probe_skipped", name=ext_name, reason="no probe configured")
                continue

            try:
                data = client.get(probe_path)
                item_count = len(data.get("value", []))
                log.info("cli.dry_run_probe_ok", name=ext_name, items=item_count)
                passed += 1
            except GraphApiError as exc:
                log.error("cli.dry_run_probe_failed", name=ext_name, error=str(exc))
                failed += 1

    log.info("cli.dry_run_complete", passed=passed, failed=failed)
    if failed > 0:
        raise SystemExit(1)


def _run_continuous(
    config: Config, token_provider: Callable[[], str], storage: StorageBackend, sync_state: SyncState, names: list[str]
) -> None:
    """Run extractors continuously on their configured intervals."""
    log.info("cli.continuous_started")

    last_run: dict[str, float] = {}
    consecutive_auth_failures = 0
    start_time = time.monotonic()
    loop_count = 0

    try:
        while True:
            now = time.time()
            loop_count += 1
            uptime = time.monotonic() - start_time

            # Determine which extractors are due
            extractors_due = []
            for ext_name in names:
                if ext_name not in EXTRACTORS:
                    continue
                _, config_getter, _ = EXTRACTORS[ext_name]
                ext_config = config_getter(config)
                if not ext_config.enabled:
                    continue
                interval_seconds = ext_config.poll_interval_minutes * 60
                if now - last_run.get(ext_name, 0) >= interval_seconds:
                    extractors_due.append(ext_name)

            log.info(
                "cli.continuous_heartbeat",
                loop=loop_count,
                uptime_seconds=round(uptime, 1),
                extractors_due=len(extractors_due),
            )

            try:
                with GraphClient(config.graph, token_provider) as client:
                    for ext_name in extractors_due:
                        module, config_getter, needs_converters = EXTRACTORS[ext_name]
                        ext_config = config_getter(config)

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

                consecutive_auth_failures = 0
            except Exception as exc:
                consecutive_auth_failures += 1
                log.error(
                    "cli.auth_failure",
                    error=str(exc),
                    consecutive_failures=consecutive_auth_failures,
                    max_failures=config.service.max_consecutive_auth_failures,
                )
                if consecutive_auth_failures >= config.service.max_consecutive_auth_failures:
                    log.critical(
                        "cli.max_auth_failures_reached",
                        consecutive_failures=consecutive_auth_failures,
                    )
                    raise SystemExit(1) from None

            time.sleep(config.service.continuous_poll_seconds)

    except KeyboardInterrupt:
        log.info("cli.continuous_stopped")
