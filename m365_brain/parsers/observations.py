"""`- [category] content #tag (context)` -- the one structured line format.

An observation is a fact the author chose to state about the document's subject.
The list-item grammar is deliberately narrow so that ordinary prose bullets stay
prose: a bullet becomes an observation only if it carries an explicit
`[category]` or at least one `#tag`.

The regexes are the grammar, not policy, so they are module constants. The one
value that *is* policy -- what to call a category the author did not name --
arrives as `ObservationConfig`.
"""

from __future__ import annotations

import re

from m365_brain.config.index import ObservationConfig
from m365_brain.model import Observation

# `[Role] Engineer` -- the category may not itself contain brackets or parens,
# which is what keeps `[[Wikilink]]` and `[text](url)` out.
CATEGORY_RE = re.compile(r"^\[([^\[\]()]+)\]\s+(.+)")
EMPTY_CATEGORY_RE = re.compile(r"^\[\]\s+(.+)")
MARKDOWN_LINK_RE = re.compile(r"^\[.*?\]\(.*?\)$")
BARE_WIKILINK_RE = re.compile(r"^\[\[.*?\]\]$")
INLINE_TAG_RE = re.compile(r"(?:^|\s)#(\w[\w\-]*)")

# `- [x] Done`. Shared with the relation parser: a checklist item is not an
# observation and not a relation, and one regex is one definition of "task".
TASK_ITEM_RE = re.compile(r"^\[[ xX\-]\]")


def parse_observations(body: str, config: ObservationConfig) -> list[Observation]:
    """Extract every observation line from a markdown body, in document order."""
    results: list[Observation] = []
    for raw_line in body.splitlines():
        line = raw_line.strip()
        if not line.startswith("- "):
            continue
        content = line[2:].strip()
        if not content:
            continue

        if TASK_ITEM_RE.match(content):
            continue
        if MARKDOWN_LINK_RE.match(content):
            continue
        if BARE_WIKILINK_RE.match(content):
            continue

        categorised = _split_category(content, config.default_category)
        if categorised is None:
            continue
        category, rest = categorised

        rest, context = _split_context(rest)
        rest, tags = _split_tags(rest)

        results.append(Observation(category=category, content=rest, tags=tags, context=context))
    return results


def _split_category(content: str, default_category: str) -> tuple[str, str] | None:
    """`(category, remainder)`, or None when the line is ordinary prose."""
    match = CATEGORY_RE.match(content)
    if match:
        return match.group(1).strip(), match.group(2).strip()

    empty = EMPTY_CATEGORY_RE.match(content)
    if empty:
        return default_category, empty.group(1).strip()

    # An untagged bullet is prose. A bullet carrying a `#tag` was written to be
    # found, so it is indexed under the default category.
    if any(part.startswith("#") for part in content.split()):
        return default_category, content
    return None


def _split_context(rest: str) -> tuple[str, str | None]:
    """Peel a trailing `(...)` off as context.

    Not when it contains a wikilink: `- knows [[Bob]] (see [[Meeting]])` ends in
    a parenthesised *relation*, and swallowing it as free text would lose the edge.
    """
    if not rest.endswith(")"):
        return rest, None
    open_paren = rest.rfind("(")
    if open_paren <= 0:
        return rest, None
    candidate = rest[open_paren + 1 : -1].strip()
    if not candidate or "[[" in candidate:
        return rest, None
    return rest[:open_paren].strip(), candidate


def _split_tags(rest: str) -> tuple[str, list[str]]:
    """Lift `#tags` out of the content into their own field."""
    tags = INLINE_TAG_RE.findall(rest)
    for tag in tags:
        rest = rest.replace(f"#{tag}", "").strip()
    return rest, tags
