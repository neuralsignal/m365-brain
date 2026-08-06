"""`index catalog …` -- the non-markdown files the index knows about.

Nested under `index` rather than sitting at the top level because the catalog
lives in the index database and shares its lifecycle; as a peer of `run` it
would read like a separate subsystem, which it is not.

Five verbs. `extract` was held back while nothing populated the catalog with a
*pending* row -- a verb whose whole job is to advance a state machine no
producer feeds is a facade over an empty table. Registration at the storage
boundary (`index/catalog_storage.py`) is that producer, so the state machine
now has rows to advance and the verb is real.

`read` remains the no-producer path: it converts a file named on the command
line and prints the markdown, touching neither the catalog nor the vault.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import click

from m365_brain.commands._context import NotFound, emit, emit_capped, open_workspace, require_config, row_limit
from m365_brain.config import ConfigError, StorageConfig, require_section
from m365_brain.config.vault import VaultConfig
from m365_brain.index.catalog_extract import CatalogConversionError, converted_output_path, extract_pending
from m365_brain.model import CatalogEntry, CatalogQuery
from m365_brain.storage import create_storage, local_base_path, resolve_key, storage_key
from m365_brain.storage.base import StorageBackend
from m365_brain.storage.exceptions import StorageError

RESOLVE_SAMPLE = 10
"""How many matches `resolve` fetches before calling a query ambiguous.

Not config: it changes no answer, only how many names the ambiguity error can
list. Two would be enough to decide; ten makes the message useful."""


@click.group("catalog")
def catalog() -> None:
    """List, search, convert and read the catalogued source files."""


def _query(
    extension: str | None,
    extractor: str | None,
    status: str | None,
    modified_after: str | None,
    name_contains: str | None,
    limit: int,
) -> CatalogQuery:
    return CatalogQuery(
        extension=extension,
        extractor=extractor,
        status=status,
        modified_after=modified_after,
        name_contains=name_contains,
        limit=limit,
    )


@catalog.command("list")
@click.option("--ext", "extension", type=str, default=None, help="e.g. .pdf")
@click.option("--extractor", type=str, default=None, help="The extractor that registered it, e.g. email")
@click.option("--status", type=str, default=None, help="A value from index.catalog.conversion_states")
@click.option("--modified-after", type=str, default=None, help="ISO timestamp")
@click.option("--limit", type=int, default=None, show_default="index.search.page_size", help="How many rows to return")
@click.option("--stats", is_flag=True, help="Print counts per conversion state instead of rows")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON on stdout")
@click.pass_context
def list_entries(
    ctx: click.Context,
    extension: str | None,
    extractor: str | None,
    status: str | None,
    modified_after: str | None,
    limit: int | None,
    stats: bool,
    as_json: bool,
) -> None:
    """Catalogued files, filtered."""
    if stats and any(filter_ is not None for filter_ in (extension, extractor, status, modified_after)):
        # `stats()` counts the whole table. Accepting filters beside it would
        # answer a question nobody asked and look like an answer to the one
        # they did -- which is only detectable once the table has rows.
        raise click.UsageError("--stats counts the whole catalog; it cannot be combined with a filter")
    config = require_config(ctx)
    query = _query(extension, extractor, status, modified_after, None, row_limit(config, limit))
    with open_workspace(config) as workspace:
        store = workspace.catalog()
        if stats:
            counts = store.stats()
            emit(as_json, counts, [f"{state}\t{count}" for state, count in sorted(counts.items())])
            return
        entries, total = store.search(query), store.count(query)
    _emit_entries(entries, total, query.limit, config.storage, as_json)


@catalog.command("search")
@click.argument("query")
@click.option("--status", type=str, default=None, help="A value from index.catalog.conversion_states")
@click.option("--limit", type=int, default=None, show_default="index.search.page_size", help="How many rows to return")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON on stdout")
@click.pass_context
def search(ctx: click.Context, query: str, status: str | None, limit: int | None, as_json: bool) -> None:
    """Catalogued files whose name contains QUERY."""
    config = require_config(ctx)
    catalog_query = _query(None, None, status, None, query, row_limit(config, limit))
    with open_workspace(config) as workspace:
        store = workspace.catalog()
        entries, total = store.search(catalog_query), store.count(catalog_query)
    _emit_entries(entries, total, catalog_query.limit, config.storage, as_json)


@catalog.command("resolve")
@click.argument("query")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON on stdout")
@click.pass_context
def resolve(ctx: click.Context, query: str, as_json: bool) -> None:
    """The source path of exactly one catalogued file. Ambiguity is an error.

    QUERY is a file name, or a path this command family printed. Matching on
    `file_name` alone meant `resolve` and `search` printed a path that neither
    would then accept, so feeding either its own output found nothing.
    """
    config = require_config(ctx)
    storage = config.storage
    with open_workspace(config) as workspace:
        store = workspace.catalog()
        # The exact `original_path` lookup the backend has always had, and no
        # verb exposed. It is tried first so a printed path resolves to its own
        # row rather than falling through to a substring match on the filename.
        entry = store.get(storage_key(storage, query))
        entries = [] if entry is not None else store.search(_query(None, None, None, None, query, RESOLVE_SAMPLE))

    if entry is None:
        entry = _one_of(entries, query, storage)
    address = resolve_key(storage, entry.original_path)
    emit(as_json, {"original_path": address}, [address])


def _one_of(entries: list[CatalogEntry], query: str, storage: StorageConfig) -> CatalogEntry:
    """The single entry `query` names, or an error saying why there is not one."""
    if not entries:
        raise NotFound(f"no catalogued file matches {query!r}")
    exact = [entry for entry in entries if entry.file_name == query]
    if len(exact) == 1:
        # An exact filename is not ambiguous just because it is also a
        # substring of longer names: `report.pdf` should resolve even when
        # `report.pdf.backup` sits beside it in the same vault.
        return exact[0]
    if len(entries) > 1:
        # The search is capped, so the count is a floor, not a total. Reporting
        # it as a total told a caller with 200 matches that it had 10.
        count = f"at least {len(entries)}" if len(entries) == RESOLVE_SAMPLE else str(len(entries))
        raise ConfigError(
            f"{query!r} matches {count} files: {[resolve_key(storage, e.original_path) for e in entries]}. "
            "Narrow the query -- resolving to the first would be a coin flip."
        )
    return entries[0]


@catalog.command("extract")
@click.option(
    "--limit",
    type=int,
    default=None,
    show_default="index.search.page_size",
    help="How many rows this pass may convert",
)
@click.option(
    "--retry-failed",
    is_flag=True,
    help="Include rows that failed before. Without it they are recorded once and left alone.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit JSON on stdout")
@click.pass_context
def extract(ctx: click.Context, limit: int | None, retry_failed: bool, as_json: bool) -> None:
    """Convert catalogued files that have not been converted yet.

    `total` is what was eligible when the pass began, so `returned < total`
    means the limit stopped it with rows still queued -- run it again.
    """
    config = require_config(ctx)
    vault = require_section(config.vault, "vault")
    # Before anything is opened: a backend this verb cannot read from should
    # say so, not fail later inside a storage client's constructor.
    source_root = local_base_path(config.storage)
    storage = create_storage(config.storage)
    rows = row_limit(config, limit)
    with open_workspace(config) as workspace:
        store = workspace.catalog()
        stats = extract_pending(
            store,
            workspace.config.index.catalog,
            _converter(source_root, storage, vault, config.converters.model_dump()),
            rows,
            retry_failed,
        )
    payload = {"converted": stats.converted, "failed": stats.failed}
    line = f"converted={stats.converted} failed={stats.failed}"
    emit_capped(as_json, payload, stats.attempted, stats.eligible, rows, [line])


def _converter(
    source_root: Path, storage: StorageBackend, vault: VaultConfig, converters: dict
) -> Callable[[CatalogEntry], str]:
    """Read one catalogued binary, convert it, write the markdown beside it.

    Every exception a converter can raise is translated to
    `CatalogConversionError` here rather than caught by the extract loop: that
    loop lives under `index/`, which may not import the Microsoft 365
    converters and therefore cannot name their failures.
    """

    def convert(entry: CatalogEntry) -> str:
        from m365_brain.m365.converters.document import DocumentConversionError, convert_document

        output = converted_output_path(
            entry.original_path, vault.filenames.attachments, vault.filenames.attachments_converted
        )
        try:
            markdown = convert_document(file_path=source_root / entry.original_path, converters_config=converters)
            storage.write_file(output, markdown)
        except (DocumentConversionError, ImportError, StorageError, OSError) as exc:
            raise CatalogConversionError(f"{type(exc).__name__}: {exc}") from exc
        return output

    return convert


@catalog.command("read")
@click.argument("path", type=str)
@click.pass_context
def read(ctx: click.Context, path: str) -> None:
    """Convert a source file to markdown and print it. Writes nothing.

    PATH is absolute, or vault-relative -- either form this command family
    prints. It is never resolved against the process CWD: `click.Path` did
    that, so the identical argument named a different file from a different
    directory, and the one string `catalog resolve` exists to produce failed
    from anywhere but the vault root.
    """
    from m365_brain.m365.converters.document import convert_document

    config = require_config(ctx)
    source = _source_file(config.storage, path)
    click.echo(convert_document(file_path=source, converters_config=config.converters.model_dump()))


def _source_file(storage_config: StorageConfig, path: str) -> Path:
    """One PATH argument as a file on disk. Absolute as given, else vault-relative."""
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = local_base_path(storage_config) / path
    if not candidate.is_file():
        raise click.UsageError(
            f"no readable file at {candidate}. PATH is an absolute path, or one relative to the "
            f"vault root -- the form `index catalog resolve` and `index catalog search` print. It "
            f"is never resolved against the current directory."
        )
    return candidate


def _emit_entries(entries: list[CatalogEntry], total: int, limit: int, storage: StorageConfig, as_json: bool) -> None:
    """Rows, with both printed paths resolved before either output shape sees them."""
    rows = [
        {
            "id": entry.entry_id,
            "file_name": entry.file_name,
            "original_path": resolve_key(storage, entry.original_path),
            "extension": entry.extension,
            "extractor": entry.extractor,
            "size_bytes": entry.size_bytes,
            "modified_at": entry.modified_at,
            "conversion_status": entry.conversion_status,
            "output_path": None if entry.output_path is None else resolve_key(storage, entry.output_path),
            "error": entry.error,
        }
        for entry in entries
    ]
    emit_capped(
        as_json,
        {"entries": rows},
        len(rows),
        total,
        limit,
        [f"{row['conversion_status']}\t{row['extension']}\t{row['original_path']}" for row in rows],
    )
