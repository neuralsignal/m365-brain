"""What every verb shares: exit codes, config loading, and wiring.

No verb builds a storage backend, a state store or a token provider itself.
Each of those is one function here, so "how a CLI invocation reaches the
library" has one implementation and a new verb cannot quietly assemble a
slightly different one.

**Output contract, and it is what keeps the bundled skills thin:** results go
to stdout, logs go to stderr through structlog. Every read verb takes `--json`.
A caller therefore never parses human text and never has to separate log noise
from data.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from typing import Any

import click

from m365_brain.config import Config, ConfigError, load_config, require_section
from m365_brain.cycle import Runtime, open_runtime
from m365_brain.logging_config import configure_logging
from m365_brain.m365.auth.profiles import AuthProfiles
from m365_brain.m365.auth.token_provider import make_cli_token_provider
from m365_brain.state import JsonStateStore, StateStore
from m365_brain.storage import create_storage
from m365_brain.vault.paths import state_directory
from m365_brain.workspace import Workspace

EXIT_OK = 0
EXIT_FAILURE = 1
"""An extractor, the index step, a hook, a push or a reconcile failed."""
EXIT_USAGE = 2
"""Click's own. The command line was wrong."""
EXIT_CONFIG = 3
"""Bad YAML, a missing key, an unknown extractor/outbox/area name, or a hook
that cannot be resolved. Distinct from 1 so a supervisor can tell "you typed it
wrong" from "Graph is down" without scraping a message."""
EXIT_AUTH = 4
"""No usable token. The answer is `auth login`, not a retry."""

CONFIG_KEY = "config_path"


def config_path(ctx: click.Context) -> str:
    """The `--config` value, or exit 3 saying so.

    `--config` is optional at the group level only because `init` creates the
    file and therefore cannot require it to exist. Every other verb calls this.
    """
    path = ctx.obj.get(CONFIG_KEY) if ctx.obj else None
    if not path:
        raise ConfigError("--config is required for this command (only `init` runs without one)")
    return path


def require_config(ctx: click.Context) -> Config:
    """Load and validate the config named on the command line.

    Every config-taking verb funnels through here, which is why the logging
    setup lives here too: `run` and `extract` used to be the only callers of
    `configure_logging`, so every other verb ran at structlog's default level
    and renderer. Doing it at the one funnel means a verb added tomorrow is
    covered without remembering to.
    """
    config = load_config(config_path(ctx))
    configure_logging(config.service.log_level, config.service.json_logs)
    return config


def token_provider(config: Config) -> Callable[[], str]:
    """The extractor path's token source, from `extractors.auth_profile`.

    `None` means the single-app deployment -- authenticate with the `auth:`
    section itself. A named profile means one of `auth.profiles`.

    Built on first use, not here. MSAL performs authority discovery in its
    constructor, so an eager build turns *assembling* a runtime into a network
    call -- which is how `run --once` against a config with a typo'd unit name
    ends up reporting an authority error instead of the config error. Memoised,
    because one MSAL app per process is one in-memory token cache per process.
    """
    built: list[Callable[[], str]] = []

    def token() -> str:
        if not built:
            name = config.extractors.auth_profile
            built.append(
                make_cli_token_provider(config.auth)
                if name is None
                else AuthProfiles(config.auth.profiles or {}).provider(name)
            )
        return built[0]()

    return token


def state_store(config: Config) -> StateStore:
    """The vault's state store. One place decides where state lives."""
    return JsonStateStore(state_directory(require_section(config.vault, "vault")))


def build_runtime(config: Config) -> Runtime:
    """Storage, state, tokens and resolved hooks, ready to run a cycle."""
    return open_runtime(config, create_storage(config.storage), state_store(config), token_provider(config))


def open_workspace(config: Config) -> Workspace:
    """The index facade, opened. The caller closes it."""
    workspace = Workspace(config)
    workspace.backend.initialize()
    return workspace


def emit(as_json: bool, payload: Any, lines: Sequence[str]) -> None:
    """One result, either as JSON or as the given human lines. Always stdout."""
    if as_json:
        click.echo(json.dumps(payload, indent=2, default=str, sort_keys=True))
        return
    for line in lines:
        click.echo(line)


def comma_list(value: str | None) -> list[str] | None:
    """`--only a,b` -> `["a", "b"]`. `None` stays `None`, meaning "all"."""
    if value is None:
        return None
    names = [item.strip() for item in value.split(",") if item.strip()]
    if not names:
        raise ConfigError("--only was given but names no units")
    return names
