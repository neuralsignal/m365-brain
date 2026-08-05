"""`index catalog …` -- the non-markdown files the index knows about.

Nested under `index` rather than sitting at the top level because the catalog
lives in the index database and shares its lifecycle; as a peer of `run` it
would read like a separate subsystem, which it is not.

Four verbs, not the six the source scripts had. `extract` (convert a file and
write the result back into the catalog) is absent because nothing populates the
catalog with a *pending* row yet -- shipping a verb whose whole job is to
advance a state machine no producer feeds would be a facade over an empty
table. `read` converts a path on disk and prints the markdown, which is what a
caller actually needs today and needs no producer at all.
"""

from __future__ import annotations

from pathlib import Path

import click

from m365_brain.commands._context import emit, open_workspace, require_config
from m365_brain.config import ConfigError
from m365_brain.model import CatalogEntry, CatalogQuery


@click.group("catalog")
def catalog() -> None:
    """List, search and read the catalogued source files."""


def _query(
    extension: str | None,
    source: str | None,
    status: str | None,
    modified_after: str | None,
    name_contains: str | None,
    limit: int,
) -> CatalogQuery:
    return CatalogQuery(
        extension=extension,
        source=source,
        status=status,
        modified_after=modified_after,
        name_contains=name_contains,
        limit=limit,
    )


@catalog.command("list")
@click.option("--ext", "extension", type=str, default=None, help="e.g. .pdf")
@click.option("--source", type=str, default=None, help="Where the file came from")
@click.option("--status", type=str, default=None, help="A value from index.catalog.conversion_states")
@click.option("--modified-after", type=str, default=None, help="ISO timestamp")
@click.option("--limit", type=int, default=100)
@click.option("--stats", is_flag=True, help="Print counts per conversion state instead of rows")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON on stdout")
@click.pass_context
def list_entries(
    ctx: click.Context,
    extension: str | None,
    source: str | None,
    status: str | None,
    modified_after: str | None,
    limit: int,
    stats: bool,
    as_json: bool,
) -> None:
    """Catalogued files, filtered."""
    with open_workspace(require_config(ctx)) as workspace:
        store = workspace.catalog()
        if stats:
            counts = store.stats()
            emit(as_json, counts, [f"{state}\t{count}" for state, count in sorted(counts.items())])
            return
        entries = store.search(_query(extension, source, status, modified_after, None, limit))
    _emit_entries(entries, as_json)


@catalog.command("search")
@click.argument("query")
@click.option("--status", type=str, default=None, help="A value from index.catalog.conversion_states")
@click.option("--limit", type=int, default=100)
@click.option("--json", "as_json", is_flag=True, help="Emit JSON on stdout")
@click.pass_context
def search(ctx: click.Context, query: str, status: str | None, limit: int, as_json: bool) -> None:
    """Catalogued files whose name contains QUERY."""
    with open_workspace(require_config(ctx)) as workspace:
        entries = workspace.catalog().search(_query(None, None, status, None, query, limit))
    _emit_entries(entries, as_json)


@catalog.command("resolve")
@click.argument("query")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON on stdout")
@click.pass_context
def resolve(ctx: click.Context, query: str, as_json: bool) -> None:
    """The source path of exactly one catalogued file. Ambiguity is an error."""
    with open_workspace(require_config(ctx)) as workspace:
        entries = workspace.catalog().search(_query(None, None, None, None, query, 10))
    if not entries:
        raise ConfigError(f"no catalogued file matches {query!r}")
    if len(entries) > 1:
        raise ConfigError(
            f"{query!r} matches {len(entries)} files: {[e.file_name for e in entries]}. "
            "Narrow the query -- resolving to the first would be a coin flip."
        )
    emit(as_json, {"original_path": entries[0].original_path}, [entries[0].original_path])


@catalog.command("read")
@click.argument("path", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.pass_context
def read(ctx: click.Context, path: Path) -> None:
    """Convert a source file to markdown and print it. Writes nothing."""
    from m365_brain.m365.converters.document import convert_document

    config = require_config(ctx)
    click.echo(convert_document(file_path=path, converters_config=config.converters.model_dump()))


def _emit_entries(entries: list[CatalogEntry], as_json: bool) -> None:
    payload = {
        "entries": [
            {
                "id": entry.entry_id,
                "file_name": entry.file_name,
                "original_path": entry.original_path,
                "extension": entry.extension,
                "source": entry.source,
                "size_bytes": entry.size_bytes,
                "modified_at": entry.modified_at,
                "conversion_status": entry.conversion_status,
                "output_path": entry.output_path,
                "error": entry.error,
            }
            for entry in entries
        ]
    }
    emit(as_json, payload, [f"{e.conversion_status}\t{e.extension}\t{e.original_path}" for e in entries])
