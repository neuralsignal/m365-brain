"""Drive items: resolve a site and a library, read an item, write one safely.

Ported from a working SharePoint writer with exactly one behavioural change,
and it is the whole point of the module.

The original exposed `put_file(..., if_match: str | None)`. `None` meant
"overwrite unconditionally", and its one production caller computed the value
as `meta.get("etag")` -- so a sidecar missing its key produced `None`, and a
file somebody had hand-edited was silently replaced. The bug was not in the
Graph call; it was in the nullable. A nullable that means "skip the safety
check" gets passed `None` eventually.

So the nullable is gone from the public surface. `create_file` writes only
where nothing exists, `update_file` writes only under an eTag, and there is no
third function. "A stale eTag raises rather than overwriting" is then a
property of the signatures rather than of remembering to pass an argument.
"""

from __future__ import annotations

import urllib.parse
from dataclasses import dataclass

import structlog

from m365_brain.config import UploadConfig
from m365_brain.m365.client import GraphClient
from m365_brain.m365.errors import GraphApiError, GraphConflictError, GraphNotFoundError
from m365_brain.m365.upload import upload_in_chunks

log = structlog.get_logger()


@dataclass(frozen=True)
class FilePayload:
    """The bytes and their MIME type, bundled so write callsites carry one object."""

    content: bytes
    content_type: str


class ETagRequired(ValueError):
    """`update_file` was called without an eTag. There is no unconditional write."""


def encode_path(item_path: str) -> str:
    """Percent-encode a drive-item path, keeping `/` as a separator.

    Required by Graph's `root:/{path}:` addressing for any folder with a space
    or a non-ASCII character in its name -- which, in a document library named
    by humans, is most of them.
    """
    return urllib.parse.quote(item_path, safe="/")


def _item_ref(drive_id: str, item_path: str) -> str:
    return f"/drives/{drive_id}/root:/{encode_path(item_path)}"


def resolve_site_id(client: GraphClient, site_hostname: str, site_path: str) -> str:
    """Resolve a site id from `hostname` + server-relative path, via colon addressing."""
    path = f"/sites/{site_hostname}:/{encode_path(site_path)}"
    site_id = client.get(path, None).get("id")
    if not site_id:
        raise GraphApiError(f"site lookup {path} returned no id", None)
    return str(site_id)


def resolve_drive_id(client: GraphClient, site_id: str, library_name: str) -> tuple[str, bool]:
    """Resolve the drive to write into. Returns `(drive_id, library_is_folder)`.

    A document library takes one of two shapes and the caller cannot tell which
    from config: a top-level library is its own drive, while a library created
    as a folder lives inside the site's default drive. The bool says which,
    and a `True` means the caller prepends `library_name/` to item paths.
    """
    drives = client.get(f"/sites/{site_id}/drives", None).get("value", [])
    for drive in drives:
        if drive.get("name") == library_name:
            return str(drive["id"]), False

    try:
        default = client.get(f"/sites/{site_id}/drive", None)
    except GraphNotFoundError as exc:
        raise GraphApiError(
            f"no drive named {library_name!r} and no default document library under site "
            f"{site_id}; drives seen: {[d.get('name') for d in drives]}",
            404,
        ) from exc
    return str(default["id"]), True


def resolve_default_drive_id(client: GraphClient, site_id: str) -> str:
    """The site's default document-library drive id."""
    drive_id = client.get(f"/sites/{site_id}/drive", None).get("id")
    if not drive_id:
        raise GraphApiError(f"default document library for site {site_id} returned no id", None)
    return str(drive_id)


def list_children(client: GraphClient, drive_id: str, folder_path: str) -> list[dict]:
    """Immediate children of a folder, following `@odata.nextLink`.

    Each child carries `name`, `id`, `lastModifiedDateTime` and the
    pre-authenticated `downloadUrl` when Graph supplies one. Raises
    `GraphNotFoundError` when the folder itself is missing.
    """
    children: list[dict] = []
    next_path: str | None = f"{_item_ref(drive_id, folder_path)}:/children"
    while next_path is not None:
        payload = client.get(next_path, None)
        children.extend(
            {
                "name": item.get("name"),
                "id": item.get("id"),
                "lastModifiedDateTime": item.get("lastModifiedDateTime"),
                "downloadUrl": item.get("@microsoft.graph.downloadUrl"),
            }
            for item in payload.get("value", [])
        )
        next_path = payload.get("@odata.nextLink")
    return children


def item_etag(client: GraphClient, drive_id: str, item_path: str) -> str | None:
    """The item's current eTag, or None when it does not exist.

    Metadata only -- `get_file` downloads the body, and asking "does this
    exist?" should not cost a download.
    """
    try:
        meta = client.get(_item_ref(drive_id, item_path), None)
    except GraphNotFoundError:
        return None
    return str(meta.get("eTag", ""))


def download_file_bytes(client: GraphClient, drive_id: str, item_path: str) -> bytes:
    """Raw bytes of a drive item. Raises `GraphNotFoundError` when it is gone."""
    return client.get_bytes(f"{_item_ref(drive_id, item_path)}:/content")


def get_file(client: GraphClient, drive_id: str, item_path: str) -> tuple[str, str] | None:
    """Return `(text, etag)`, or None when the item does not exist.

    The eTag is the half that matters: it is the token `update_file` needs, and
    reading content without it is how a caller ends up with no way to write
    back safely.
    """
    etag = item_etag(client, drive_id, item_path)
    if etag is None:
        log.info("graph.files.item_not_found", drive_id=drive_id[:20], item_path=item_path)
        return None
    return download_file_bytes(client, drive_id, item_path).decode("utf-8"), etag


def create_file(
    client: GraphClient,
    upload: UploadConfig,
    drive_id: str,
    item_path: str,
    payload: FilePayload,
) -> str:
    """Create an item that must not already exist. Returns the new eTag.

    Raises `GraphConflictError` when it does exist. Graph's simple `/content`
    PUT is create-or-replace and `conflictBehavior: fail` is only available on
    the upload-session path, so this reads first.

    # ponytail: read-before-create is not atomic. The concurrent-edit case that
    # actually costs data is covered by `update_file`'s eTag; upgrade path is a
    # session-only create with `conflictBehavior: fail` if this ever races.
    """
    existing = item_etag(client, drive_id, item_path)
    if existing is not None:
        raise GraphConflictError(
            f"create_file refused: {item_path!r} already exists in drive {drive_id} "
            f"(eTag {existing}); use update_file with that eTag to overwrite it",
            412,
        )
    return _write(client, upload, drive_id, item_path, payload, None)


def update_file(
    client: GraphClient,
    upload: UploadConfig,
    drive_id: str,
    item_path: str,
    payload: FilePayload,
    etag: str,
) -> str:
    """Overwrite an existing item under `If-Match`. Returns the new eTag.

    `etag` is required and non-empty: an empty one raises `ETagRequired` before
    any request is made, so a caller that lost the eTag gets a crash rather
    than an overwrite. A remote change since the read raises
    `GraphConflictError` (HTTP 412) and nothing is written.
    """
    if not etag:
        raise ETagRequired(
            f"update_file({item_path!r}) needs the eTag read at fetch time. "
            "Pass it, or call create_file if the item is new -- there is no unconditional write."
        )
    return _write(client, upload, drive_id, item_path, payload, etag)


def _write(
    client: GraphClient,
    upload: UploadConfig,
    drive_id: str,
    item_path: str,
    payload: FilePayload,
    if_match: str | None,
) -> str:
    """The one write path. Private, so `if_match: str | None` never escapes."""
    ref = _item_ref(drive_id, item_path)
    if len(payload.content) <= upload.simple_upload_max_bytes:
        response = client.put_bytes(f"{ref}:/content", payload.content, payload.content_type, if_match)
        return str(response.json().get("eTag", ""))
    return _write_session(client, upload, drive_id, item_path, payload.content, if_match)


def _write_session(
    client: GraphClient,
    upload: UploadConfig,
    drive_id: str,
    item_path: str,
    content: bytes,
    if_match: str | None,
) -> str:
    """Upload above the simple-PUT ceiling, via a chunked session.

    Graph's upload session takes no `If-Match` and its chunk PUTs cannot carry
    one either, so the conditional write is re-checked immediately before the
    session opens. That is strictly weaker than `If-Match` and it is said out
    loud here rather than hidden: a change landing inside the window is not
    caught. It still turns the common case -- someone edited the file
    yesterday -- from a silent clobber into a raised conflict.
    """
    if if_match is not None:
        current = item_etag(client, drive_id, item_path)
        if current != if_match:
            raise GraphConflictError(
                f"eTag for {item_path!r} moved from {if_match} to {current} before the upload "
                "session opened; nothing was written",
                412,
            )
    session = client.post(
        f"{_item_ref(drive_id, item_path)}:/createUploadSession",
        {"item": {"@microsoft.graph.conflictBehavior": "replace"}},
    )
    final = upload_in_chunks(
        session.json()["uploadUrl"],
        content,
        upload.chunk_bytes,
        client.config.timeout_seconds,
        client.config.error_message_max_length,
    )
    return str(final.json().get("eTag", ""))
