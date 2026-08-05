"""The one slug policy for frontmatter tags.

The builders used to run two: `slugify` for permalinks, and a hand-rolled
`.lower().replace(" ", "-")` for tags. The hand-rolled one left accents, slashes
and punctuation in place, so `Archive/Old Projects` became the tag
`archive/old-projects` while the permalink for the same mail read
`archive-old-projects`. One policy, one function, one place to change it.
"""

from __future__ import annotations

from m365_brain.m365.markdown_writer import slugify


def tag_slug(value: str, max_length: int) -> str | None:
    """`value` as a tag, or None when it carries nothing sluggable.

    `slugify` substitutes `untitled` for an empty result -- a sensible filename
    stem and a useless tag, so that case yields None and the caller omits the
    tag entirely. `max_length` is passed by the caller for the same reason the
    permalink call sites pass it: the number belongs beside the thing it sizes.

    A value that genuinely reads `Untitled` loses its tag too. Give `slugify` a
    no-sentinel mode if that ever costs anything.
    """
    slug = slugify(value, max_length)
    return None if slug == "untitled" else slug
