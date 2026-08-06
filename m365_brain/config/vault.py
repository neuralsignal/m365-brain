"""The `vault:` section -- every directory and filename the vault uses.

Not one of these names is a constant in code. `inbox`, `_meta`, `index.md`,
`messages.jsonl` and the eight per-extractor directory names are all here, so
the layout is the operator's decision rather than the author's habit.
"""

from __future__ import annotations

from pydantic import BaseModel, model_validator

from m365_brain.config.base import SECTION_MODEL_CONFIG
from m365_brain.config.extractors import EXTRACTOR_NAMES


class VaultLayout(BaseModel):
    """Top-level directory names under `vault.root`.

    `processed`, `failed` and `inflight` are the outbox archive segments;
    `state` and `manifests` sit under `meta`.

    `failed` was `rejected` until the dispatch vocabulary moved off that word --
    see `vault/dispatch.py`. Only the *key* changed: the directory name is still
    whatever the operator puts here, so nothing on disk has to move.
    """

    model_config = SECTION_MODEL_CONFIG
    inbox: str
    annotations: str
    outbox: str
    meta: str
    processed: str
    failed: str
    inflight: str
    state: str
    manifests: str


class VaultFilenames(BaseModel):
    """Filenames the extractors write, and the two attachment directory names."""

    model_config = SECTION_MODEL_CONFIG
    entry: str
    conversation: str
    conversation_store: str
    attachments: str
    attachments_converted: str


class VaultConfig(BaseModel):
    model_config = SECTION_MODEL_CONFIG
    root: str
    layout: VaultLayout
    extractor_dirs: dict[str, str]
    filenames: VaultFilenames

    @model_validator(mode="after")
    def _extractor_dirs_cover_every_extractor(self) -> VaultConfig:
        configured = set(self.extractor_dirs)
        known = set(EXTRACTOR_NAMES)
        missing = sorted(known - configured)
        unknown = sorted(configured - known)
        if missing:
            raise ValueError(f"vault.extractor_dirs is missing a directory name for: {missing}")
        if unknown:
            raise ValueError(
                f"vault.extractor_dirs names extractors this package does not implement: {unknown}. "
                f"Known extractors: {sorted(known)}"
            )
        return self
