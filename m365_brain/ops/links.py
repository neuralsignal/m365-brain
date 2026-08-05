"""Which never-resolved wikilinks point at an entity the index already holds.

A link written as `[[contact-anna-meier]]` before the note it names exists
stays unresolved forever: `resolve_relations` matches on title, permalink and
alias, and the slug is none of those. This module answers the question that
follows -- *is there an entity that link plainly means?* -- and answers it as a
report. It never rewrites a file. Rewriting is a judgement about somebody's
prose, and a `medium` match is exactly the case where a human should look.

**The lookup is built from the index, not from a directory.** The script this
replaces walked a folder, took each filename to be the person's name, and
special-cased its generated `_index.md`. All three assumptions are gone: a
candidate is an entity of `ops.link_resolution.target_type`, wherever it lives,
and its title is a parsed field rather than a file stem.

Confidence is derived from *which* lookup matched, never from a tunable score.
There is no threshold to set, so there is no threshold in config.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from m365_brain.config.ops import LinkResolutionConfig
from m365_brain.index.backends.base import IndexBackend, TextQuery
from m365_brain.model import EntityRef
from m365_brain.ops.names import deslugify, email_addresses, name_key

Confidence = Literal["high", "medium", "unresolved"]
"""How the match was made, in one word.

`high` is an email address or a title reproduced exactly; `medium` is a match
that survived normalisation, so word order, case or accents differed;
`unresolved` is no match at all.
"""


@dataclass(frozen=True, slots=True)
class LinkResolution:
    """One unresolved link, and the entity it appears to mean."""

    source: EntityRef
    link_text: str
    """The relation target as written, prefix included."""

    matched: EntityRef | None
    confidence: Confidence


def indexed_entities(backend: IndexBackend, entity_type: str | None, page_size: int) -> list[EntityRef]:
    """Every indexed entity, optionally of one type, as one list.

    A filter-only `TextQuery` is the protocol's listing call -- there is no
    `all_entities`, deliberately, because a store that could only be read
    exhaustively would push every consumer into loading the corpus. Paging is
    done here so the three operations in this subpackage share one reading of
    what "the whole index" means.
    """
    collected: list[EntityRef] = []
    page = 1
    while True:
        found = backend.text_search(
            TextQuery(fts=None, entity_type=entity_type, tag=None, metadata=(), page=page, page_size=page_size)
        )
        collected.extend(hit.entity for hit in found.hits)
        if not found.hits or len(collected) >= found.total:
            return collected
        page += 1


@dataclass(frozen=True, slots=True)
class _Lookups:
    """The three indexes a link is tried against, in confidence order."""

    by_address: dict[str, EntityRef]
    by_title: dict[str, EntityRef]
    by_name: dict[str, EntityRef]


def resolve_links(backend: IndexBackend, config: LinkResolutionConfig, page_size: int) -> list[LinkResolution]:
    """Every unresolved link carrying the configured prefix, with its verdict."""
    entities = indexed_entities(backend, None, page_size)
    sources = {entity.entity_id: entity for entity in entities}
    lookups = _build_lookups(backend, [e for e in entities if e.entity_type == config.target_type])

    resolutions: list[LinkResolution] = []
    for edge in backend.outgoing_relations(sorted(sources)):
        if edge.to_entity_id is not None or not edge.to_name.startswith(config.unresolved_prefix):
            continue
        source = sources.get(edge.from_entity_id)
        if source is None:
            continue
        matched, confidence = _match(edge.to_name.removeprefix(config.unresolved_prefix), lookups)
        resolutions.append(
            LinkResolution(source=source, link_text=edge.to_name, matched=matched, confidence=confidence)
        )
    return resolutions


def _build_lookups(backend: IndexBackend, candidates: Sequence[EntityRef]) -> _Lookups:
    """Index the candidates under every spelling a link might use.

    First writer wins in each map. A second entity claiming an address or a
    normalised name is an ambiguity in the corpus, and quietly preferring the
    later one would make the report depend on iteration order.
    """
    by_address: dict[str, EntityRef] = {}
    by_title: dict[str, EntityRef] = {}
    by_name: dict[str, EntityRef] = {}
    for candidate in candidates:
        by_title.setdefault(candidate.title, candidate)
        by_name.setdefault(name_key(candidate.title), candidate)
        for observation in backend.get_observations(candidate.entity_id):
            for address in email_addresses(observation.content):
                by_address.setdefault(address, candidate)
    return _Lookups(by_address=by_address, by_title=by_title, by_name=by_name)


def _match(remainder: str, lookups: _Lookups) -> tuple[EntityRef | None, Confidence]:
    """Try the three lookups in order and report which one answered."""
    address_hit = lookups.by_address.get(remainder.strip().casefold())
    if address_hit is not None:
        return address_hit, "high"

    spelled = deslugify(remainder)
    title_hit = lookups.by_title.get(spelled)
    if title_hit is not None:
        return title_hit, "high"

    name_hit = lookups.by_name.get(name_key(spelled))
    if name_hit is not None:
        return name_hit, "medium"

    return None, "unresolved"
