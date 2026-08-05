"""`auth login` and `auth status`, over the named-profile registry."""

from __future__ import annotations

import click

from m365_brain.commands._context import EXIT_AUTH, emit, require_config
from m365_brain.config import AuthProfileConfig, ConfigError
from m365_brain.m365.auth.profiles import AuthProfiles, ProfileStatus


def _profiles(config) -> AuthProfiles:
    """Every named profile, plus the `auth:` section itself under `default`.

    The single-app deployment names no profile at all, and `auth status` still
    has to have something to report on it.
    """
    named = dict(config.auth.profiles or {})
    named.setdefault("default", AuthProfileConfig(**config.auth.model_dump(exclude={"profiles"})))
    return AuthProfiles(named)


@click.group()
def auth() -> None:
    """Authenticate an Entra app profile, and report what is cached."""


@auth.command("login")
@click.option("--profile", "name", required=True, type=str, help="A name under auth.profiles")
@click.pass_context
def login(ctx: click.Context, name: str) -> None:
    """Run the device-code flow for one profile (interactive)."""
    config = require_config(ctx)
    profiles = _profiles(config)
    if name not in profiles.names():
        raise ConfigError(f"no auth profile named {name!r}; configured: {profiles.names()}")
    profiles.login(name)
    status = profiles.status(name)
    click.echo(f"{name}: {status.state}")
    click.echo(f"  accounts: {', '.join(status.accounts) or 'none'}")
    click.echo(f"  scopes:   {' '.join(sorted(status.scopes))}")


@auth.command("status")
@click.option("--profile", "name", type=str, default=None, help="One profile; absent means all")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON on stdout")
@click.pass_context
def status(ctx: click.Context, name: str | None, as_json: bool) -> None:
    """Report each profile's cache state without prompting anybody."""
    config = require_config(ctx)
    profiles = _profiles(config)
    if name is not None and name not in profiles.names():
        raise ConfigError(f"no auth profile named {name!r}; configured: {profiles.names()}")

    reports = [profiles.status(each) for each in ([name] if name else profiles.names())]
    emit(as_json, {"profiles": [_as_dict(report) for report in reports]}, [_line(report) for report in reports])
    if any(report.state != "authenticated" for report in reports):
        raise SystemExit(EXIT_AUTH)


def _as_dict(status: ProfileStatus) -> dict:
    return {
        "name": status.name,
        "state": status.state,
        "valid": status.state == "authenticated",
        "accounts": list(status.accounts),
        "scopes": sorted(status.scopes),
        "token_cache_path": status.token_cache_path,
    }


def _line(status: ProfileStatus) -> str:
    accounts = ", ".join(status.accounts) or "-"
    return f"{status.name:<16} {status.state:<20} {accounts}"
