"""`[[Wikilink]]` edges, in both spellings the corpus uses.

Two grammars, deliberately kept apart:

* **explicit** -- a list item whose text before the link names the edge:
  `- works_at [[Acme]] (since 2019)`. A bare `- [[Acme]]` names no edge, so it
  gets `relations.explicit_default_type`.
* **inline** -- a `[[Wikilink]]` anywhere in prose. It gets
  `relations.inline_type`, a weaker claim than an explicit edge and worth
  distinguishing at query time.

A list item that contains a wikilink is only ever read as an explicit relation:
without that rule every explicit edge would also be emitted as an inline one.

Edges are emitted unresolved (`to_entity_id=None`). Resolution is the index's
job -- a link may point at a file that does not exist yet, and a parser that
needed the index to run could not parse a single file on a cold start.
"""

from __future__ import annotations

import re

from m365_brain.config.index import RelationConfig
from m365_brain.model import Relation
from m365_brain.parsers.observations import TASK_ITEM_RE

WIKILINK_RE = re.compile(r"\[\[([^\[\]]+)\]\]")


def parse_relations(body: str, config: RelationConfig) -> list[Relation]:
    """Every edge out of this document, de-duplicated on `(type, target)`.

    Explicit edges are collected first, so a link that appears both as a list
    item and again in prose keeps the named type rather than the inline one.
    """
    explicit = _parse_explicit(body, config.explicit_default_type)
    inline = _parse_inline(body, config.inline_type)

    seen: set[tuple[str, str]] = set()
    results: list[Relation] = []
    for relation in explicit + inline:
        key = (relation.relation_type, relation.to_name)
        if key in seen:
            continue
        seen.add(key)
        results.append(relation)
    return results


def _parse_explicit(body: str, default_type: str) -> list[Relation]:
    results: list[Relation] = []
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line.startswith("- "):
            continue
        content = line[2:].strip()
        if "[[" not in content or "]]" not in content:
            continue
        if TASK_ITEM_RE.match(content):
            continue

        start = content.index("[[")
        end = content.index("]]", start)
        target = content[start + 2 : end].strip()

        # Quote characters are never a relation type. A frontmatter list of
        # wikilinks renders as `- '[[Name]]'`, and the bare `'` in front of the
        # link used to become the edge's type -- an edge no config would ever
        # spell, so a reader asking for that relation got an empty result that
        # reads as "no such links" rather than as a defect.
        relation_type = content[:start].strip().strip("'\"").strip() or default_type

        context = None
        trailing = content[end + 2 :].strip()
        if trailing.startswith("(") and trailing.endswith(")"):
            context = trailing[1:-1].strip()

        results.append(Relation(relation_type=relation_type, to_name=target, to_entity_id=None, context=context))
    return results


def _parse_inline(body: str, inline_type: str) -> list[Relation]:
    results: list[Relation] = []
    for raw_line in body.splitlines():
        if raw_line.strip().startswith("- ") and "[[" in raw_line:
            continue  # already read as an explicit relation
        for match in WIKILINK_RE.finditer(raw_line):
            results.append(
                Relation(
                    relation_type=inline_type,
                    to_name=match.group(1).strip(),
                    to_entity_id=None,
                    context=None,
                )
            )
    return results
