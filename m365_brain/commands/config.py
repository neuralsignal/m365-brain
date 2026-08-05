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


SECRET_KEYS = frozenset({"client_secret", "connection_string", "secret_key", "fernet_key"})
"""Config keys whose value never reaches stdout.

Redacted by name rather than by type because the schema declares these as
plain `str`. That is a weaker guarantee than a `SecretStr` would give, so the
test for this verb asserts the *values* are absent from the output rather than
asserting this list is right -- a list can be typo'd, and a value either leaked
or it did not. Promoting these four fields to `SecretStr` is filed as its own
change; it touches every consumer that reads them."""

REDACTED = "***"


@config_group.command("show")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON on stdout")
@click.pass_context
def show(ctx: click.Context, as_json: bool) -> None:
    """The merged, env-expanded, path-resolved config, with secrets redacted."""
    payload = redact(require_config(ctx).model_dump(mode="json"))
    emit(as_json, payload, _yaml_lines(payload))


def redact(value: object) -> object:
    """Replace every secret-named leaf, at any depth. Non-null values only."""
    if isinstance(value, dict):
        return {
            key: REDACTED if key in SECRET_KEYS and inner is not None else redact(inner) for key, inner in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value


def _present(loaded) -> list[str]:
    return [name for name, value in loaded.model_dump().items() if value is not None]


def _yaml_lines(payload: dict) -> list[str]:
    import yaml

    return yaml.safe_dump(payload, sort_keys=True, default_flow_style=False).splitlines()
