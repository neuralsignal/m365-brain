"""Runtime sections: `hooks:`, `manifest:`, and `m365.upload:`.

Hook specs are validated for *shape* only. Config parsing must stay pure -- a
validator that imports arbitrary third-party modules turns reading a config
file into arbitrary code execution. Resolution happens later, at workspace
open, where an ImportError is a startup failure rather than a parse failure.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, field_validator, model_validator

from m365_brain.config.base import SECTION_MODEL_CONFIG

_HOOK_SPEC = re.compile(r"^[A-Za-z_]\w*(\.[A-Za-z_]\w*)*:[A-Za-z_]\w*$")

# Graph requires every upload-session chunk to be a multiple of 320 KiB.
UPLOAD_CHUNK_MULTIPLE_BYTES = 320 * 1024


def is_hook_spec(value: str) -> bool:
    """True when `value` has the `module.path:callable` shape.

    The colon is load-bearing: `a.b.c` cannot say whether `c` is a submodule or
    an attribute, so a bare dotted path is ambiguous and rejected.
    """
    return _HOOK_SPEC.match(value) is not None


def _check_specs(field: str, specs: list[str]) -> list[str]:
    bad = [spec for spec in specs if not is_hook_spec(spec)]
    if bad:
        raise ValueError(f"hooks.{field} entries must be 'module.path:callable' -- rejected: {bad}")
    return specs


class HooksConfig(BaseModel):
    model_config = SECTION_MODEL_CONFIG
    post_cycle: list[str]
    post_reconcile: list[str]

    @field_validator("post_cycle")
    @classmethod
    def _post_cycle_shape(cls, value: list[str]) -> list[str]:
        return _check_specs("post_cycle", value)

    @field_validator("post_reconcile")
    @classmethod
    def _post_reconcile_shape(cls, value: list[str]) -> list[str]:
        return _check_specs("post_reconcile", value)


class ManifestConfig(BaseModel):
    """How many cycle manifests to keep, and what the pointer file is called."""

    model_config = SECTION_MODEL_CONFIG
    retain_cycles: int
    latest_filename: str


class UploadConfig(BaseModel):
    model_config = SECTION_MODEL_CONFIG
    inline_attachment_max_bytes: int
    simple_upload_max_bytes: int
    chunk_bytes: int

    @model_validator(mode="after")
    def _chunk_is_a_graph_multiple(self) -> UploadConfig:
        if self.chunk_bytes % UPLOAD_CHUNK_MULTIPLE_BYTES != 0:
            raise ValueError(
                f"m365.upload.chunk_bytes ({self.chunk_bytes}) must be a multiple of "
                f"{UPLOAD_CHUNK_MULTIPLE_BYTES} (320 KiB) -- the upload-session API rejects anything else"
            )
        return self


class M365Config(BaseModel):
    model_config = SECTION_MODEL_CONFIG
    upload: UploadConfig
