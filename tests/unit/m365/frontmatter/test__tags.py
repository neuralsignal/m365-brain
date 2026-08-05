"""One slug policy for tags, and it is the permalink's.

The builders used to hand-roll `.lower().replace(" ", "-")` for tags, which left
accents, slashes and punctuation in place while the permalink beside them was
fully slugified. These are the cases where the two used to disagree.
"""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from m365_brain.m365.frontmatter._tags import tag_slug
from m365_brain.m365.markdown_writer import slugify


class TestTagSlug:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("Sent Items", "sent-items"),
            ("Archive/Old Projects", "archive-old-projects"),
            ("R&D Team", "r-d-team"),
            ("Zürich", "zurich"),
            ("ALREADY-slug", "already-slug"),
        ],
        ids=["spaces", "separator", "punctuation", "accents", "mixed-case"],
    )
    def test_a_sluggable_value_matches_slugify(self, value: str, expected: str) -> None:
        assert tag_slug(value, 80) == expected
        assert tag_slug(value, 80) == slugify(value, 80)

    @pytest.mark.parametrize(
        "value",
        ["", "   ", "!!!", "---", "北京"],
        ids=["empty", "whitespace", "punctuation", "hyphens", "non-latin"],
    )
    def test_a_value_with_nothing_sluggable_yields_none_not_untitled(self, value: str) -> None:
        """`untitled` is a fine filename stem and a terrible tag: it would
        collide across every unrelated entity that happens to have one."""
        assert slugify(value, 80) == "untitled", "the sentinel this function exists to intercept"
        assert tag_slug(value, 80) is None

    def test_max_length_is_the_callers_to_pass(self) -> None:
        assert tag_slug("engineering and product design", 11) == "engineering"


class TestTagSlugProperties:
    @given(st.text(max_size=60), st.integers(min_value=1, max_value=120))
    def test_result_is_none_or_a_bare_slug(self, value: str, max_length: int) -> None:
        result = tag_slug(value, max_length)

        if result is None:
            return
        assert result == slugify(value, max_length)
        assert result.strip("-") == result
        assert " " not in result and "/" not in result
        assert len(result) <= max_length
