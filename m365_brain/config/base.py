"""The model policy every config section obeys.

One place, so `extra="forbid"` cannot be forgotten on a new section. A typo'd
key must fail at load naming the key -- silently ignoring it is how a config
file drifts away from the behaviour it claims to describe.
"""

from __future__ import annotations

from pydantic import ConfigDict

SECTION_MODEL_CONFIG = ConfigDict(frozen=True, strict=True, extra="forbid")
