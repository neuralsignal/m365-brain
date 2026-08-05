"""Every path in the vault, built from config and nowhere else.

Twelve sites across the extractors used to build their own path out of
hardcoded directory and file names. Each was a small, reasonable literal;
together they meant the folder contract was defined in twelve places and
changeable in none. `VaultPaths` is the one place, and a CI grep for those
names under this package is what keeps it that way -- so do not reintroduce
one even in a docstring.

Every method returns a **storage-relative POSIX string**, not a filesystem
path: `StorageBackend` takes relative string keys, so the same resolver works
against a local directory and a blob container without a branch.

`vault.root` is deliberately absent. The backend's `base_path`/`prefix` already
supplies it; prepending it here would produce `vault/vault/emails/...` on local
storage and a doubled prefix on blob.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from m365_brain.config import VaultConfig

RECEIPT_SUFFIX = ".receipt.json"
"""Distinguishes a receipt from the intent it sits beside, in one directory
listing. Not config: it is the pairing rule between two files this package
writes, not a name an operator chooses."""

RECONCILED_SUFFIX = ".reconciled.json"
"""Marks a dispatched intent whose fate is settled. Without it the
reconciliation pass re-fetches every draft it has ever sent, forever."""


class VaultPathError(ValueError):
    """Raised when a path argument would escape or corrupt the vault layout."""


def _validate_segment(segment: str, context: str) -> str:
    """Reject a path segment that would escape the vault or produce `//`.

    Ported from the folder-contract validator. `..` and a leading separator are
    the traversal cases; an empty segment is the one that silently produces a
    doubled slash and a key no backend can address.

    A bare `.` is rejected for the same reason this module exists: local storage
    lets the OS collapse `a/./b` to `a/b`, blob storage keeps it as a distinct
    key, and the same call then addresses two different objects.
    """
    if not segment:
        raise VaultPathError(f"empty path segment in {context}")
    normalised = segment.replace("\\", "/")
    if normalised.startswith("/"):
        raise VaultPathError(f"path segment cannot start with a separator: {segment!r} in {context}")
    for part in normalised.split("/"):
        if not part:
            raise VaultPathError(f"empty path segment in {segment!r} ({context})")
        if part == "..":
            raise VaultPathError(f"parent traversal disallowed: {segment!r} in {context}")
        if part == ".":
            raise VaultPathError(f"current-directory segment disallowed: {segment!r} in {context}")
    return normalised


def _join(context: str, *segments: str) -> str:
    """Validate every segment, then join with `/`. Empty segments are dropped.

    A dropped empty *leading* segment is how the Teams extractors ask for a
    conversation-relative path (`attachments/{msg}/{name}`) from the same
    method that builds the absolute one -- see `attachment`.
    """
    parts = [_validate_segment(segment, context) for segment in segments if segment != ""]
    if not parts:
        raise VaultPathError(f"no path segments given for {context}")
    return "/".join(parts)


@dataclass(frozen=True)
class VaultPaths:
    """Resolves vault paths from `VaultConfig`. Holds no state and no I/O."""

    vault: VaultConfig

    # --- inbox: upstream truth, rewritten by the extractors ----------------

    def extractor_dir(self, extractor: str) -> str:
        """The configured directory name for one extractor.

        Raises rather than falling back: an unknown extractor here means the
        caller and `vault.extractor_dirs` disagree, and inventing a name would
        write a subtree nothing ever purges.
        """
        try:
            return self.vault.extractor_dirs[extractor]
        except KeyError:
            known = sorted(self.vault.extractor_dirs)
            raise VaultPathError(f"no vault.extractor_dirs entry for {extractor!r}; configured: {known}") from None

    def inbox_root(self, extractor: str) -> str:
        """`inbox/<extractor dir>` -- the root one extractor owns."""
        return _join("VaultPaths.inbox_root", self.vault.layout.inbox, self.extractor_dir(extractor))

    def inbox_item(self, extractor: str, *segments: str) -> str:
        """`inbox/<extractor dir>/<segments...>` -- one item's directory or file."""
        return _join("VaultPaths.inbox_item", self.vault.layout.inbox, self.extractor_dir(extractor), *segments)

    # --- the files an item directory contains ------------------------------

    def entry_file(self, item_dir: str) -> str:
        """The single markdown file inside a per-item directory."""
        return _join("VaultPaths.entry_file", item_dir, self.vault.filenames.entry)

    def conversation_file(self, conv_dir: str) -> str:
        """The rendered conversation timeline -- a derived artifact."""
        return _join("VaultPaths.conversation_file", conv_dir, self.vault.filenames.conversation)

    def conversation_store(self, conv_dir: str) -> str:
        """The append-and-upsert message store -- the source of truth."""
        return _join("VaultPaths.conversation_store", conv_dir, self.vault.filenames.conversation_store)

    def attachment(self, item_dir: str, *segments: str) -> str:
        """A downloaded attachment under an item directory.

        `item_dir` may be `""`, which yields the item-relative form the Teams
        renderer links to (`attachments/<msg id>/<name>`). One method for both
        because the two must not drift.
        """
        return _join("VaultPaths.attachment", item_dir, self.vault.filenames.attachments, *segments)

    def converted_attachment(self, item_dir: str, *segments: str) -> str:
        """The markdown conversion of an attachment. `item_dir` may be `""`."""
        return _join("VaultPaths.converted_attachment", item_dir, self.vault.filenames.attachments_converted, *segments)

    # --- the other three subtrees ------------------------------------------

    def annotations(self, *segments: str) -> str:
        """Agent-authored content, parallel to the inbox and never overwritten by it."""
        return _join("VaultPaths.annotations", self.vault.layout.annotations, *segments)

    def outbox(self, outbox_name: str) -> str:
        """The directory an outbox's pending intents are written to."""
        return _join("VaultPaths.outbox", self.vault.layout.outbox, outbox_name)

    def outbox_intent(self, outbox_name: str, uuid: str) -> str:
        """One pending intent. The filename stem is the intent's uuid, by contract."""
        return _join("VaultPaths.outbox_intent", self.vault.layout.outbox, outbox_name, f"{uuid}.md")

    def inflight(self, uuid: str) -> str:
        """A claimed intent whose outcome is not yet known. Never auto-retried."""
        return self.meta(self.vault.layout.inflight, f"{uuid}.md")

    def processed(self, uuid: str) -> str:
        """A dispatched intent, archived byte-identical. This tree is the ledger."""
        return self.meta(self.vault.layout.processed, f"{uuid}.md")

    def rejected(self, uuid: str) -> str:
        """A blocked or failed intent, archived byte-identical beside its receipt."""
        return self.meta(self.vault.layout.rejected, f"{uuid}.md")

    def processed_receipt(self, uuid: str) -> str:
        """The receipt sidecar beside a dispatched intent.

        A sidecar, not injected frontmatter, so the archived intent still
        parses under its own `extra="forbid"` and can serve as the fixture
        reconciliation diffs against.
        """
        return self.meta(self.vault.layout.processed, f"{uuid}{RECEIPT_SUFFIX}")

    def rejected_receipt(self, uuid: str) -> str:
        """The receipt sidecar beside a blocked or failed intent.

        Two methods rather than one taking an archive argument: the caller
        already knows which archive it is writing to, and an argument it could
        get wrong would put the receipt somewhere nothing looks.
        """
        return self.meta(self.vault.layout.rejected, f"{uuid}{RECEIPT_SUFFIX}")

    def reconciled(self, uuid: str) -> str:
        """The terminal-verdict marker beside a dispatched intent."""
        return self.meta(self.vault.layout.processed, f"{uuid}{RECONCILED_SUFFIX}")

    def meta(self, *segments: str) -> str:
        """Anything the vault keeps about itself: state, manifests, archives."""
        return _join("VaultPaths.meta", self.vault.layout.meta, *segments)

    def state(self, *segments: str) -> str:
        """Sync state and delta tokens, under meta."""
        return self.meta(self.vault.layout.state, *segments)

    def manifests(self, *segments: str) -> str:
        """Per-cycle manifests, under meta."""
        return self.meta(self.vault.layout.manifests, *segments)


# --- the two filesystem paths, where `vault.root` does belong ---------------
#
# Sync state and cycle manifests are facts *about* a vault rather than content
# in it: they are read before the process has a storage backend, and a
# blob-backed vault still keeps them on local disk. So they are real filesystem
# paths and they do need the root -- which is why they are free functions with
# explicit names rather than `VaultPaths` methods, which would quietly break
# the storage-relative promise the class above makes.


def state_directory(vault: VaultConfig) -> Path:
    """`<root>/<meta>/<state>` on the filesystem."""
    return Path(vault.root) / VaultPaths(vault).state()


def manifest_directory(vault: VaultConfig) -> Path:
    """`<root>/<meta>/<manifests>` on the filesystem."""
    return Path(vault.root) / VaultPaths(vault).manifests()
