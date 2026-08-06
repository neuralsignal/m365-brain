"""What every verb shares: exit codes, config loading, and wiring.

No verb builds a storage backend, a state store or a token provider itself.
Each of those is one function here, so "how a CLI invocation reaches the
library" has one implementation and a new verb cannot quietly assemble a
slightly different one.

**Output contract, and it is what keeps the bundled skills thin:** results go
to stdout, logs go to stderr through structlog. Every read verb takes `--json`.
A caller therefore never parses human text and never has to separate log noise
from data.

**Every path printed is fully resolved.** A storage key is relative by
contract, and `emit` is the one place all of them cross the process boundary,
so the base is joined on here rather than at each call site -- a verb added
tomorrow cannot forget. `catalog resolve` printed a key that `catalog read`
then resolved against the *process CWD*, so the same string named different
files from different directories and "does not exist" was the diagnosis for
both. Nothing the CLI prints should need a base the caller has to know.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from typing import Any

import click

from m365_brain.config import Config, ConfigError, StorageConfig, load_config, require_section
from m365_brain.cycle import Runtime, open_runtime
from m365_brain.logging_config import configure_logging
from m365_brain.m365.auth.profiles import AuthProfiles
from m365_brain.m365.auth.token_provider import make_cli_token_provider
from m365_brain.state import JsonStateStore, StateStore
from m365_brain.storage import create_storage, resolve_key
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
EXIT_NOT_FOUND = 5
"""The corpus holds nothing matching what you asked for.

Distinct from 3, which says the *configuration* is wrong. A query that matches
nothing is a fact about the data, and answering it with "your config is
invalid" sends the reader to edit a file that is fine.

Only the verbs that promise exactly one answer raise it -- `index context`,
`catalog resolve`. The list-shaped verbs (`search`, `list`) keep returning 0
with an empty result, because "no rows" is an ordinary answer to a search.
"""


class NotFound(Exception):
    """Raised by a verb that must resolve exactly one thing and found none."""


CONFIG_KEY = "config_path"
LOADED_CONFIG = "config"
"""Where `require_config` parks what it loaded, so `emit` can reach the same
object without every verb threading it through a third argument."""

STORAGE_PATH_KEYS = frozenset({"path", "original_path", "output_path"})
"""Payload keys whose value is a storage-relative key.

Deliberately not "every key ending in `_path`". `file_path` is relative to an
*index root*, which is a different base and one the payload does not name;
joining the storage base onto it would produce a confident wrong answer where
today there is an obvious missing one. `base_path`, `db_path` and
`token_cache_path` are already absolute -- the config loader resolves them --
so they need no entry and would be no-ops if they had one.
"""


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

    The result is parked on the context because `emit` needs the same object to
    resolve the paths it prints. Re-reading the file there could resolve a path
    against a config the verb never validated.
    """
    if ctx.obj is not None and LOADED_CONFIG in ctx.obj:
        return ctx.obj[LOADED_CONFIG]
    config = load_config(config_path(ctx))
    configure_logging(config.service.log_level, config.service.json_logs)
    if ctx.obj is not None:
        ctx.obj[LOADED_CONFIG] = config
    return config


def loaded_config() -> Config | None:
    """The config this invocation loaded, if it loaded one.

    `None` only for a verb that never called `require_config` -- today that is
    `init` alone, which creates the config file and prints nothing but the
    absolute paths it just made.
    """
    ctx = click.get_current_context(silent=True)
    if ctx is None or not ctx.obj:
        return None
    return ctx.obj.get(LOADED_CONFIG)


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


def resolve_payload_paths(payload: Any, storage: StorageConfig) -> Any:
    """Every `STORAGE_PATH_KEYS` value in `payload`, resolved. Recurses."""
    if isinstance(payload, dict):
        return {
            key: resolve_key(storage, value)
            if key in STORAGE_PATH_KEYS and isinstance(value, str)
            else resolve_payload_paths(value, storage)
            for key, value in payload.items()
        }
    if isinstance(payload, list):
        return [resolve_payload_paths(item, storage) for item in payload]
    return payload


def emit(as_json: bool, payload: Any, lines: Sequence[str]) -> None:
    """One result, either as JSON or as the given human lines. Always stdout.

    Storage keys in `payload` are resolved on the way out. Human `lines` are
    already-formatted strings and cannot be walked, so a verb that prints a
    path in human mode resolves it itself and builds the line from the same
    value it puts in the payload -- `resolve_key` is idempotent, so the two
    agree by construction rather than by discipline.
    """
    config = loaded_config()
    if config is not None:
        payload = resolve_payload_paths(payload, config.storage)
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
