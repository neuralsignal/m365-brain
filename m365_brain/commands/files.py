"""`files pull` and `files push` -- one SharePoint document, both directions.

`push` requires `--if-match`. There is no unconditional overwrite here and
there is none in the library either: the write path routes on whether an eTag
is present, so an intent cannot ask for one at all. A 412 raises; it is never
retried by widening the condition.

The two verbs address a document by its four config-free coordinates rather
than by a named target, because a target registry would be a second place
document locations live -- and an intent file already carries exactly these
fields.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import click

from m365_brain.commands._context import emit, require_config
from m365_brain.config import Config, require_section
from m365_brain.m365.auth.profiles import AuthProfiles
from m365_brain.m365.client import GraphClient
from m365_brain.m365.files import FilePayload, get_file, resolve_drive_id, resolve_site_id, update_file


@click.group("files")
def files_group() -> None:
    """Read and write a single SharePoint document through Graph."""


def _client(config: Config, profile: str) -> GraphClient:
    return GraphClient(
        config.graph,
        AuthProfiles(config.auth.profiles or {}, config.graph.timeout_seconds).provider(profile),
    )


_LOCATION = [
    click.option("--profile", required=True, type=str, help="The auth profile to use"),
    click.option("--site-hostname", required=True, type=str, help="e.g. contoso.sharepoint.com"),
    click.option("--site-path", required=True, type=str, help="e.g. /sites/Team"),
    click.option("--library", "library_name", required=True, type=str, help="Document library display name"),
    click.option("--item-path", required=True, type=str, help="Path within the library"),
]


def _location[F: Callable[..., object]](command: F) -> F:
    for option in reversed(_LOCATION):
        command = option(command)
    return command


@files_group.command("pull")
@_location
@click.option("--out", "out_path", required=True, type=click.Path(dir_okay=False, path_type=Path))
@click.option("--json", "as_json", is_flag=True, help="Emit JSON on stdout")
@click.pass_context
def pull(
    ctx: click.Context,
    profile: str,
    site_hostname: str,
    site_path: str,
    library_name: str,
    item_path: str,
    out_path: Path,
    as_json: bool,
) -> None:
    """Download a document and print the eTag needed to write it back."""
    config = require_config(ctx)
    with _client(config, profile) as client:
        site_id = resolve_site_id(client, site_hostname, site_path)
        drive_id, _ = resolve_drive_id(client, site_id, library_name)
        found = get_file(client, drive_id, item_path)
    if found is None:
        raise SystemExit(f"no document at {item_path!r} in {library_name!r}")
    content, etag = found
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content, encoding="utf-8")
    # Absolute, because `--out report.md` otherwise prints a path only the
    # caller's CWD explains -- and `emit` would then read it as a vault key.
    payload = {"path": str(out_path.resolve()), "bytes": len(content.encode("utf-8")), "etag": etag}
    emit(as_json, payload, [f"{payload['bytes']} bytes -> {payload['path']}", f"etag: {etag}"])


@files_group.command("push")
@_location
@click.option("--in", "in_path", required=True, type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--if-match", "etag", required=True, type=str, help="The eTag from `files pull`")
@click.option("--content-type", required=True, type=str, help="e.g. text/markdown")
@click.option("--json", "as_json", is_flag=True, help="Emit JSON on stdout")
@click.pass_context
def push(
    ctx: click.Context,
    profile: str,
    site_hostname: str,
    site_path: str,
    library_name: str,
    item_path: str,
    in_path: Path,
    etag: str,
    content_type: str,
    as_json: bool,
) -> None:
    """Write a document back, only if it has not changed since `--if-match`."""
    config = require_config(ctx)
    upload = require_section(config.m365, "m365").upload
    with _client(config, profile) as client:
        site_id = resolve_site_id(client, site_hostname, site_path)
        drive_id, _ = resolve_drive_id(client, site_id, library_name)
        new_etag = update_file(
            client, upload, drive_id, item_path, FilePayload(in_path.read_bytes(), content_type), etag
        )
    emit(as_json, {"etag": new_etag}, [f"etag: {new_etag}"])
