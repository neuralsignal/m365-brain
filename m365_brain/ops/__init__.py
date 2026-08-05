"""The operational reports: unresolved links, relationship tiers, inbox triage.

Layer 6 -- one pass over the index, exactly like `sync` is one pass over the
extractors. Nothing here imports the Microsoft half of the package, and nothing
here writes to the corpus: all three operations read the index and return
values. The command layer decides how to print them.

Every window, threshold, prefix list and tier boundary comes from
`config.ops`. That is not a style preference: a heuristic that cannot be
expressed as config is a heuristic the operator cannot see, and the three
scripts this subpackage absorbed were nine tenths invisible heuristic.
"""

from __future__ import annotations

from m365_brain.ops.links import Confidence, LinkResolution, indexed_entities, resolve_links
from m365_brain.ops.names import deslugify, email_addresses, name_key, normalize_name, reverse_comma_name
from m365_brain.ops.tiers import DAYS_PER_MONTH, TierAssignment, assign_rung, compute_tiers, is_stale
from m365_brain.ops.triage import (
    TriageItem,
    is_cc_only,
    is_forward,
    rejected_references,
    triage,
)

__all__ = [
    "DAYS_PER_MONTH",
    "Confidence",
    "LinkResolution",
    "TierAssignment",
    "TriageItem",
    "assign_rung",
    "compute_tiers",
    "deslugify",
    "email_addresses",
    "indexed_entities",
    "is_cc_only",
    "is_forward",
    "is_stale",
    "name_key",
    "normalize_name",
    "rejected_references",
    "resolve_links",
    "reverse_comma_name",
    "triage",
]
