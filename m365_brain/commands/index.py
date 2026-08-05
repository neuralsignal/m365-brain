"""`index …` -- sync, rebuild, search, context, recent, validate, delete, move.

Every verb here is option parsing, one facade call, and formatting. Any `if`
that is not about output shape belongs in `m365_brain/index/`.
"""

from __future__ import annotations

from datetime import UTC, datetime

import click

from m365_brain.commands._catalog import catalog
from m365_brain.commands._context import EXIT_FAILURE, emit, open_workspace, require_config
from m365_brain.config import ConfigError, require_section
from m365_brain.index.query import parse_metadata_filter
from m365_brain.index.search import SearchFilters
from m365_brain.index_step import run_index_step
from m365_brain.model import SearchPage
from m365_brain.state import InMemoryStateStore
from m365_brain.vault.paths import VaultPaths


@click.group("index")
def index_group() -> None:
    """Sync, search and navigate the markdown index."""


index_group.add_command(catalog)


@index_group.command("sync")
@click.option("--root", "roots", multiple=True, help="Limit to named roots; absent means all")
@click.pass_context
def sync(ctx: click.Context, roots: tuple[str, ...]) -> None:
    """Bring the index in line with the files under the configured roots."""
    _run_index(ctx, roots, full_rebuild=False)


@index_group.command("rebuild")
@click.option("--root", "roots", multiple=True, help="Limit to named roots; absent means all")
@click.option("--yes", is_flag=True, required=True, help="Required: this reparses every file")
@click.pass_context
def rebuild(ctx: click.Context, roots: tuple[str, ...], yes: bool) -> None:
    """Reparse and reindex every file, ignoring checksums."""
    _run_index(ctx, roots, full_rebuild=True)


def _run_index(ctx: click.Context, roots: tuple[str, ...], full_rebuild: bool) -> None:
    config = require_config(ctx)
    index = require_section(config.index, "index")
    if roots:
        index = index.model_copy(update={"roots": _narrow(index, roots)})
    outcome = run_index_step(index, InMemoryStateStore(), datetime.now(UTC), full_rebuild)
    click.echo(
        f"roots={','.join(outcome.roots)} indexed={outcome.indexed} skipped={outcome.skipped} "
        f"pruned={outcome.pruned} errors={outcome.errors} elapsed={outcome.elapsed_seconds}s"
    )
    if outcome.errors:
        raise SystemExit(EXIT_FAILURE)


def _narrow(index, names: tuple[str, ...]):
    known = {root.name: root for root in index.roots}
    unknown = sorted(set(names) - set(known))
    if unknown:
        raise ConfigError(f"unknown index root(s): {unknown}; configured: {sorted(known)}")
    return [known[name] for name in names]


@index_group.command("search")
@click.argument("query", required=False)
@click.option("--search-type", "mode", type=click.Choice(["text", "vector", "hybrid"]), default="text")
@click.option("--type", "entity_type", type=str, default=None, help="Restrict to one entity type")
@click.option("--tag", type=str, default=None, help="Restrict to one tag")
@click.option("--field", "fields", multiple=True, help="Metadata filter, e.g. status=open (repeatable)")
@click.option("--limit", type=int, default=None, help="Cap the hits printed; absent means the page size")
@click.option("--page", type=int, default=1, help="1-based page number")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON on stdout")
@click.pass_context
def search(
    ctx: click.Context,
    query: str | None,
    mode: str,
    entity_type: str | None,
    tag: str | None,
    fields: tuple[str, ...],
    limit: int | None,
    page: int,
    as_json: bool,
) -> None:
    """Full-text, semantic or hybrid search over the index."""
    config = require_config(ctx)
    filters = SearchFilters(
        entity_type=entity_type,
        tag=tag,
        metadata=tuple(parse_metadata_filter(expression) for expression in fields),
    )
    with open_workspace(config) as workspace:
        results = workspace.search(query, mode, filters, page)
    _emit_page(results, limit, as_json)


def _emit_page(results: SearchPage, limit: int | None, as_json: bool) -> None:
    hits = results.hits if limit is None else results.hits[:limit]
    payload = {
        "total": results.total,
        "page": results.page,
        "page_size": results.page_size,
        "results": [
            {
                "permalink": hit.entity.permalink,
                "title": hit.entity.title,
                "type": hit.entity.entity_type,
                "file_path": hit.entity.file_path,
                "updated_at": hit.entity.updated_at,
                "score": hit.score,
                "snippet": hit.snippet,
            }
            for hit in hits
        ],
    }
    emit(as_json, payload, [f"{hit.entity.permalink}\t{hit.entity.entity_type}\t{hit.entity.title}" for hit in hits])


@index_group.command("context")
@click.argument("entity", required=False)
@click.option("--permalink", type=str, default=None, help="Look the entity up by permalink")
@click.option("--depth", type=int, default=1, help="How many hops to traverse")
@click.option("--format", "shape", type=click.Choice(["text", "json"]), default="text")
@click.pass_context
def context(ctx: click.Context, entity: str | None, permalink: str | None, depth: int, shape: str) -> None:
    """One entity, its observations, and everything within `--depth` hops."""
    if (entity is None) == (permalink is None):
        raise click.UsageError("give either ENTITY or --permalink, not both and not neither")
    config = require_config(ctx)
    identifier = permalink or entity
    with open_workspace(config) as workspace:
        found = workspace.find(identifier, by_permalink=permalink is not None)
        if found is None:
            raise ConfigError(f"no entity matches {identifier!r}")
        observations = workspace.observations(found.entity_id)
        edges = workspace.context(found.entity_id, depth)

    payload = {
        "entity": {"permalink": found.permalink, "title": found.title, "type": found.entity_type},
        "observations": [{"category": o.category, "content": o.content, "tags": o.tags} for o in observations],
        "edges": [
            {"depth": e.depth, "direction": e.direction, "relation": e.relation_type, "to": e.to_name} for e in edges
        ],
    }
    lines = [f"# {found.title} ({found.permalink})"]
    lines += [f"- [{o.category}] {o.content}" for o in observations]
    lines += [f"{'  ' * e.depth}{e.direction} {e.relation_type} -> {e.to_name}" for e in edges]
    emit(shape == "json", payload, lines)


@index_group.command("recent")
@click.option("--timeframe", type=str, default="7d", help="e.g. 7d, 2 weeks ago")
@click.option("--type", "entity_type", type=str, default=None, help="Restrict to one entity type")
@click.option("--limit", type=int, default=20)
@click.option("--json", "as_json", is_flag=True, help="Emit JSON on stdout")
@click.pass_context
def recent(ctx: click.Context, timeframe: str, entity_type: str | None, limit: int, as_json: bool) -> None:
    """Entities updated within a timeframe, newest first."""
    config = require_config(ctx)
    with open_workspace(config) as workspace:
        entities = workspace.recent(timeframe, limit)
    if entity_type is not None:
        entities = [entity for entity in entities if entity.entity_type == entity_type]
    payload = {
        "entities": [
            {"permalink": e.permalink, "title": e.title, "type": e.entity_type, "updated_at": e.updated_at}
            for e in entities
        ]
    }
    emit(as_json, payload, [f"{e.updated_at}\t{e.entity_type}\t{e.title}" for e in entities])


@index_group.command("paths")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON on stdout")
@click.pass_context
def paths(ctx: click.Context, as_json: bool) -> None:
    """Where the index reads from, and where the vault writes to."""
    config = require_config(ctx)
    index = require_section(config.index, "index")
    vault = require_section(config.vault, "vault")
    resolver = VaultPaths(vault)
    payload = {
        "roots": {root.name: root.path for root in index.roots},
        "database": index.sqlite.path,
        "vault_root": vault.root,
        "inbox": {name: resolver.inbox_root(name) for name in sorted(vault.extractor_dirs)},
    }
    emit(as_json, payload, [f"{name}\t{path}" for name, path in sorted(payload["roots"].items())])
