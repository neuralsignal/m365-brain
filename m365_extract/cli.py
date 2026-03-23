"""CLI entry point for m365-extract.

Commands:
  m365-extract auth login       — Authenticate via device code flow
  m365-extract sync --once      — Run all enabled extractors once
  m365-extract sync --continuous — Run extractors on their configured intervals
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from pathlib import Path

import click
import structlog
from dotenv import find_dotenv, load_dotenv

from m365_extract.auth.device_code import DeviceCodeAuth
from m365_extract.auth.token_provider import make_cli_token_provider
from m365_extract.config import Config, load_config
from m365_extract.extractors import (
    calendar,
    contacts,
    directory,
    email,
    onedrive,
    sharepoint,
    teams_channels,
    teams_chats,
)
from m365_extract.graph_client import GraphClient
from m365_extract.state import SyncState
from m365_extract.storage import create_storage
from m365_extract.storage.base import StorageBackend

log = structlog.get_logger()

_EXTRACTORS = {
    "email": (email, lambda cfg: cfg.extractors.email, False),
    "calendar": (calendar, lambda cfg: cfg.extractors.calendar, False),
    "teams_chats": (teams_chats, lambda cfg: cfg.extractors.teams_chats, False),
    "teams_channels": (teams_channels, lambda cfg: cfg.extractors.teams_channels, False),
    "onedrive": (onedrive, lambda cfg: cfg.extractors.onedrive, True),
    "sharepoint": (sharepoint, lambda cfg: cfg.extractors.sharepoint, True),
    "contacts": (contacts, lambda cfg: cfg.extractors.contacts, False),
    "directory": (directory, lambda cfg: cfg.extractors.directory, False),
}


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


@main.command()
@click.option("--once", is_flag=True, help="Run all enabled extractors once and exit")
@click.option("--continuous", is_flag=True, help="Run extractors on their configured intervals")
@click.option("--extractors", "extractor_names", type=str, help="Comma-separated list of extractors to run")
@click.pass_context
def sync(ctx: click.Context, once: bool, continuous: bool, extractor_names: str | None) -> None:
    """Sync Microsoft 365 data."""
    if not once and not continuous:
        raise click.UsageError("Specify either --once or --continuous")

    config = load_config(ctx.obj["config_path"])

    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(config.service.log_level),
        ),
    )

    token_provider = make_cli_token_provider(config.auth)
    storage = create_storage(config.storage)
    sync_state = SyncState(config.state.state_file_path)

    # Determine which extractors to run
    if extractor_names:
        names = [n.strip() for n in extractor_names.split(",")]
    else:
        names = list(_EXTRACTORS.keys())

    if once:
        _run_extractors(config, token_provider, storage, sync_state, names)
    elif continuous:
        _run_continuous(config, token_provider, storage, sync_state, names)


def _run_extractors(
    config: Config, token_provider: Callable[[], str], storage: StorageBackend, sync_state: SyncState, names: list[str]
) -> None:
    """Run enabled extractors once."""
    with GraphClient(config.graph, token_provider) as client:
        for ext_name in names:
            if ext_name not in _EXTRACTORS:
                log.warning("cli.unknown_extractor", name=ext_name)
                continue

            module, config_getter, needs_converters = _EXTRACTORS[ext_name]
            ext_config = config_getter(config)

            if not ext_config.enabled:
                log.info("cli.extractor_disabled", name=ext_name)
                continue

            log.info("cli.running_extractor", name=ext_name)
            state = sync_state.load(ext_name)

            try:
                if needs_converters:
                    updated_state, count = module.run(client, storage, state, ext_config, config.converters)
                else:
                    updated_state, count = module.run(client, storage, state, ext_config)
                sync_state.save(ext_name, updated_state)
                click.echo(f"  {ext_name}: {count} items written")
            except Exception as exc:
                log.error("cli.extractor_failed", name=ext_name, error=str(exc))
                click.echo(f"  {ext_name}: FAILED - {exc}")


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
                    if ext_name not in _EXTRACTORS:
                        continue

                    module, config_getter, needs_converters = _EXTRACTORS[ext_name]
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
