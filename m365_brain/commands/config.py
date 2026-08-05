"""`config validate` and `config show`.

`validate` resolves hooks as well as parsing YAML, which is what makes it a
real preflight rather than a syntax check: an unimportable hook is the failure
most likely to survive review and least likely to be noticed until a cycle has
already run.
"""

from __future__ import annotations

import click

from m365_brain.commands._context import config_path, emit, require_config
from m365_brain.hooks import resolve_hooks


@click.group("config")
def config_group() -> None:
    """Validate and inspect the effective configuration."""


@config_group.command("validate")
@click.pass_context
def validate(ctx: click.Context) -> None:
    """Parse every file, then import every configured hook. Exit 3 on either."""
    loaded = require_config(ctx)
    specs = [] if loaded.hooks is None else [*loaded.hooks.post_cycle, *loaded.hooks.post_reconcile]
    resolve_hooks(specs)
    click.echo(f"ok: {config_path(ctx)}")
    click.echo(f"  sections: {', '.join(_present(loaded))}")
    click.echo(f"  hooks:    {len(specs)} resolved")


@config_group.command("show")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON on stdout")
@click.pass_context
def show(ctx: click.Context, as_json: bool) -> None:
    """The merged, env-expanded, path-resolved config, with secrets redacted.

    Redaction is the schema's job, not this verb's: every secret is a
    `SecretStr`, which serialises as `**********` in both output modes. The
    denylist of secret-looking key names this used to carry is gone -- it could
    fail open on a typo or on a fifth secret nobody added to it, and a type
    cannot. A `null` secret still serialises as `null`, so `config show` keeps
    saying which flow a config selects.
    """
    payload = require_config(ctx).model_dump(mode="json")
    emit(as_json, payload, _yaml_lines(payload))


def _present(loaded) -> list[str]:
    return [name for name, value in loaded.model_dump().items() if value is not None]


def _yaml_lines(payload: dict) -> list[str]:
    import yaml

    return yaml.safe_dump(payload, sort_keys=True, default_flow_style=False).splitlines()
