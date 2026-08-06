"""`m365-brain` -- the one console script.

One entry point, not two. A two-letter alias on every user's PATH buys them a
shell alias they can write themselves and costs a second name to document and
keep in step.

**`--config` is optional at the group level and required everywhere else.**
`init` creates the config file and therefore cannot demand it already exists;
every other verb calls `require_config`, which fails with exit 3. Making it
optional at the group and enforcing it per-verb is the only arrangement in
which both are true.

**Exit codes** (`CONTRACTS.md` has the table):

| 0 | success                                                                  |
| 1 | an extractor, the index step, a hook, a push or a reconcile failed       |
| 2 | usage -- Click's own                                                     |
| 3 | configuration invalid or unresolvable                                    |
| 4 | authentication required or expired beyond refresh                        |

3 and 4 exist so a supervisor can tell "you typed it wrong" and "go re-login"
apart from "Graph is down" without scraping a message. They are mapped in one
place -- the group's `invoke` -- rather than in fifteen `try` blocks.
"""

from __future__ import annotations

from importlib import resources
from pathlib import Path

import click
import structlog
from dotenv import find_dotenv, load_dotenv

from m365_brain.commands import (
    auth,
    config_group,
    files_group,
    index_group,
    ops_group,
    outbox_group,
    teams_group,
    vault_group,
)
from m365_brain.commands._context import (
    CONFIG_KEY,
    EXIT_AUTH,
    EXIT_CONFIG,
    EXIT_FAILURE,
    build_runtime,
    comma_list,
    emit,
    require_config,
)
from m365_brain.config import ConfigError, require_section
from m365_brain.cycle import Selection, run_forever, run_once, select_units
from m365_brain.hooks import HookResolutionError
from m365_brain.logging_config import route_logs_to_stderr
from m365_brain.m365.auth.profiles import AuthProfileError
from m365_brain.m365.auth.token_provider import TokenRefreshError
from m365_brain.schedule import read_cursor
from m365_brain.vault.paths import VaultPathError, VaultPaths

log = structlog.get_logger()

TEMPLATE_PACKAGE = "m365_brain.templates"
TEMPLATE_NAME = "m365-brain.yaml"
SECONDS_PER_MINUTE = 60


class ExitCodeGroup(click.Group):
    """Maps the two failure families onto their exit codes, once."""

    def invoke(self, ctx: click.Context) -> object:
        try:
            return super().invoke(ctx)
        except (ConfigError, HookResolutionError, VaultPathError) as exc:
            click.echo(f"config error: {exc}", err=True)
            raise SystemExit(EXIT_CONFIG) from exc
        except (AuthProfileError, TokenRefreshError) as exc:
            click.echo(f"authentication required: {exc}", err=True)
            raise SystemExit(EXIT_AUTH) from exc


@click.group(cls=ExitCodeGroup)
@click.option("--config", "config_path", type=str, default=None, help="Comma-separated config YAML paths")
@click.pass_context
def main(ctx: click.Context, config_path: str | None) -> None:
    """Sync Microsoft 365 into a markdown vault, index it, and write back."""
    route_logs_to_stderr()
    ctx.ensure_object(dict)
    ctx.obj[CONFIG_KEY] = config_path
    if config_path:
        first = Path(config_path.split(",")[0].strip()).resolve().parent / ".env"
        if first.exists():
            load_dotenv(first)
    load_dotenv(find_dotenv(usecwd=True))


main.add_command(auth)
main.add_command(config_group)
main.add_command(index_group)
main.add_command(ops_group)
main.add_command(outbox_group)
main.add_command(files_group)
main.add_command(teams_group)
main.add_command(vault_group)


TEMPLATE_ANCHORS = ("../vault", "../state", "../notes", "../attachments")
"""The four directory prefixes the packaged template writes its paths under.

`init` rewrites each to somewhere under `--vault`. Literal string replacement
rather than a YAML round-trip, because the template's comments *are* the
configuration reference -- `yaml.safe_dump` would silently delete the document
that explains what the operator just got.
"""


def _relocate(template_text: str, vault_dir: Path) -> str:
    """Point every templated path at the vault the operator asked for."""
    targets = {
        "../vault": vault_dir,
        "../state": vault_dir / "_meta",
        "../notes": vault_dir / "annotations",
        "../attachments": vault_dir / "attachments",
    }
    for anchor in TEMPLATE_ANCHORS:
        template_text = template_text.replace(anchor, str(targets[anchor]))
    return template_text


@main.command("init")
@click.argument("path", type=click.Path(dir_okay=False, path_type=Path))
@click.option("--vault", "vault_dir", required=True, type=click.Path(file_okay=False, path_type=Path))
def init(path: Path, vault_dir: Path) -> None:
    """Write a complete config file and create the vault directories.

    Refuses to overwrite: a config file is the one artifact an operator has
    edited by hand, and clobbering it to save a flag is not a trade.

    Paths are written absolute. Relative ones would resolve against the config
    file's directory, which is correct but invisible -- and `init` is the one
    moment where the operator has not yet learned that rule.
    """
    if path.exists():
        raise ConfigError(f"{path} already exists; delete it or choose another path")

    resolved_vault = vault_dir.resolve()
    template = resources.files(TEMPLATE_PACKAGE).joinpath(TEMPLATE_NAME)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_relocate(template.read_text(encoding="utf-8"), resolved_vault), encoding="utf-8")

    created = [str(path)]
    for name in ("inbox", "annotations", "outbox", "attachments", "_meta"):
        directory = resolved_vault / name
        directory.mkdir(parents=True, exist_ok=True)
        created.append(str(directory))
    for line in created:
        click.echo(line)


@main.command("run")
@click.option("--once", "once", is_flag=True, help="One cycle, then exit")
@click.option("--only", type=str, default=None, help="Comma-separated unit names")
@click.option("--resync", is_flag=True, help="Forget the selected extractors' delta tokens first")
@click.option("--delay-start", type=int, default=0, help="Minutes to wait before the first cycle")
@click.option("--json", "as_json", is_flag=True, help="Emit the manifest as JSON")
@click.pass_context
def run(ctx: click.Context, once: bool, only: str | None, resync: bool, delay_start: int, as_json: bool) -> None:
    """Run cycles: extract, index, then dispatch the post-cycle hooks."""
    config = require_config(ctx)
    selection = Selection(names=comma_list(only), resync=resync, ignore_schedule=once)
    select_units(config, selection.names)  # exit 3 before anything is built
    runtime = build_runtime(config)

    if not once:
        raise SystemExit(run_forever(runtime, selection, delay_start * SECONDS_PER_MINUTE))

    manifest = run_once(runtime, selection)
    emit(as_json, manifest.model_dump(mode="json"), _cycle_lines(manifest))
    if not manifest.ok:
        raise SystemExit(EXIT_FAILURE)


def _cycle_lines(manifest) -> list[str]:
    lines = [
        f"{manifest.cycle_id} ok={manifest.ok} "
        f"changes={len(manifest.paths(kind=None, extractor=None))} "
        f"extractors={len(manifest.extractors)} hooks={len(manifest.hooks)}"
    ]
    lines += [f"  ! {failure}" for failure in manifest.failures()]
    return lines


@main.command("extract")
@click.option("--only", type=str, default=None, help="Comma-separated extractor names")
@click.option("--resync", is_flag=True, help="Forget the selected extractors' delta tokens first")
@click.option("--dry-run", "dry", is_flag=True, help="Probe each extractor's endpoint, write nothing")
@click.option("--json", "as_json", is_flag=True, help="Emit the manifest as JSON")
@click.pass_context
def extract(ctx: click.Context, only: str | None, resync: bool, dry: bool, as_json: bool) -> None:
    """Run the extractors once, without the index step or the hooks."""
    from m365_brain.commands._context import token_provider
    from m365_brain.config import EXTRACTOR_NAMES
    from m365_brain.dry_run import dry_run

    config = require_config(ctx)
    names = comma_list(only)

    if dry:
        chosen = names or [name for name in EXTRACTOR_NAMES if getattr(config.extractors, name).enabled]
        dry_run(config, token_provider(config), chosen)
        return

    extractors = names or [name for name in EXTRACTOR_NAMES if getattr(config.extractors, name).enabled]
    select_units(config, extractors)  # exit 3 before anything is built
    manifest = run_once(build_runtime(config), Selection(names=extractors, resync=resync, ignore_schedule=True))
    payload = {entry.name: entry.item_count for entry in manifest.extractors}
    emit(as_json, manifest.model_dump(mode="json"), [f"{name}\t{count} item(s)" for name, count in payload.items()])
    if not manifest.ok:
        raise SystemExit(EXIT_FAILURE)


@main.command("status")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON on stdout")
@click.pass_context
def status(ctx: click.Context, as_json: bool) -> None:
    """Per-unit last run, last success, failure streak, and the last cycle."""
    from m365_brain.commands._context import state_store
    from m365_brain.cycle import select_units
    from m365_brain.manifest import ManifestStore
    from m365_brain.vault.paths import manifest_directory

    config = require_config(ctx)
    state = state_store(config)
    units = select_units(config, None)
    cursors = {unit.name: read_cursor(state, unit.name) for unit in units}

    vault = require_section(config.vault, "vault")
    latest = ManifestStore(manifest_directory(vault), require_section(config.manifest, "manifest")).latest()

    payload = {
        "units": cursors,
        "vault_root": str(VaultPaths(vault).vault.root),
        "last_cycle": None
        if latest is None
        else {
            "cycle_id": latest.cycle_id,
            "finished_at": latest.finished_at.isoformat(),
            "ok": latest.ok,
            "failures": latest.failures(),
            "hooks": [{"spec": hook.spec, "error": hook.error} for hook in latest.hooks],
        },
    }
    emit(as_json, payload, _status_lines(cursors, latest))
    unhealthy = any(int(cursor.get("consecutive_failures", 0)) for cursor in cursors.values())
    if unhealthy or (latest is not None and not latest.ok):
        raise SystemExit(EXIT_FAILURE)


def _status_lines(cursors: dict[str, dict], latest) -> list[str]:
    lines = [f"{'unit':<16} {'last run':<22} {'last success':<22} fails"]
    for name, cursor in cursors.items():
        lines.append(
            f"{name:<16} {cursor.get('last_run_at') or '-':<22} "
            f"{cursor.get('last_success_at') or '-':<22} {cursor.get('consecutive_failures', 0)}"
        )
    if latest is None:
        lines.append("no cycle has completed yet")
    else:
        lines.append(f"last cycle {latest.cycle_id} ok={latest.ok}")
        lines += [f"  ! {failure}" for failure in latest.failures()]
    return lines
