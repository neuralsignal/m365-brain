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

from m365_brain.commands._context import emit, open_workspace, require_config
from m365_brain.config import ConfigError, StorageConfig, require_section
from m365_brain.config.vault import VaultConfig
from m365_brain.index.catalog_extract import CatalogConversionError, converted_output_path, extract_pending
from m365_brain.model import CatalogEntry, CatalogQuery
from m365_brain.storage import create_storage
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
    if stats and any(filter_ is not None for filter_ in (extension, source, status, modified_after)):
        # `stats()` counts the whole table. Accepting filters beside it would
        # answer a question nobody asked and look like an answer to the one
        # they did -- which is only detectable once the table has rows.
        raise click.UsageError("--stats counts the whole catalog; it cannot be combined with a filter")
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
        entries = workspace.catalog().search(_query(None, None, None, None, query, RESOLVE_SAMPLE))
    if not entries:
        raise ConfigError(f"no catalogued file matches {query!r}")
    exact = [entry for entry in entries if entry.file_name == query]
    if len(exact) == 1:
        # An exact filename is not ambiguous just because it is also a
        # substring of longer names: `report.pdf` should resolve even when
        # `report.pdf.backup` sits beside it in the same vault.
        emit(as_json, {"original_path": exact[0].original_path}, [exact[0].original_path])
        return
    if len(entries) > 1:
        # The search is capped, so the count is a floor, not a total. Reporting
        # it as a total told a caller with 200 matches that it had 10.
        count = f"at least {len(entries)}" if len(entries) == RESOLVE_SAMPLE else str(len(entries))
        raise ConfigError(
            f"{query!r} matches {count} files: {[e.original_path for e in entries]}. "
            "Narrow the query -- resolving to the first would be a coin flip."
        )
    emit(as_json, {"original_path": entries[0].original_path}, [entries[0].original_path])


@catalog.command("extract")
@click.option("--limit", type=int, default=100, help="How many rows this pass may convert")
@click.option(
    "--retry-failed",
    is_flag=True,
    help="Include rows that failed before. Without it they are recorded once and left alone.",
)
@click.option("--json", "as_json", is_flag=True, help="Emit JSON on stdout")
@click.pass_context
def extract(ctx: click.Context, limit: int, retry_failed: bool, as_json: bool) -> None:
    """Convert catalogued files that have not been converted yet."""
    config = require_config(ctx)
    vault = require_section(config.vault, "vault")
    # Before anything is opened: a backend this verb cannot read from should
    # say so, not fail later inside a storage client's constructor.
    source_root = _local_source_root(config.storage)
    storage = create_storage(config.storage)
    with open_workspace(config) as workspace:
        store = workspace.catalog()
        stats = extract_pending(
            store,
            workspace.config.index.catalog,
            _converter(source_root, storage, vault, config.converters.model_dump()),
            limit,
            retry_failed,
        )
    payload = {"attempted": stats.attempted, "converted": stats.converted, "failed": stats.failed}
    emit(as_json, payload, [f"attempted={stats.attempted} converted={stats.converted} failed={stats.failed}"])


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


def _local_source_root(storage_config: StorageConfig) -> Path:
    """Where the catalogued binaries can be read back from.

    `StorageBackend` can write bytes but not read them, so there is no
    backend-agnostic way to hand a blob back to a converter that wants a
    filesystem path. Rather than pretend, this says so: a blob-backed vault
    runs `extract` where the vault is local, or `StorageBackend` grows the
    missing half of `write_bytes` first.
    """
    if storage_config.backend != "local" or storage_config.local is None:
        raise ConfigError(
            f"index catalog extract reads each catalogued binary back off disk, and storage.backend "
            f"is {storage_config.backend!r}. StorageBackend has write_bytes but no read_bytes, so "
            f"there is nothing to convert from -- run extract against a local vault."
        )
    return Path(storage_config.local.base_path)


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
