"""Outlook-compatible HTML and body composition.

The styles are the part a port loses silently. Outlook strips a `<style>`
block, so a fragment with no inline styles renders as unformatted text -- the
mail still sends, so no test fails and nobody notices until somebody reads the
draft. Each assertion here names the property that would disappear.
"""

from __future__ import annotations

import pytest

from m365_brain.m365.outboxes.rendering import (
    OUTLOOK_STYLES,
    compose_with_signature,
    markdown_to_outlook_html,
    merge_reply_body,
)


class TestOutlookStyles:
    @pytest.mark.parametrize(
        ("markdown", "tag"),
        [
            ("# Head", "<h1 style="),
            ("## Head", "<h2 style="),
            ("### Head", "<h3 style="),
            ("- one", "<ul style="),
            ("1. one", "<ol style="),
            ("> quoted", "<blockquote style="),
            ("---\n", "<hr style="),
            ("    code block", "<pre style="),
            ("`inline`", "<code style="),
            ("| a | b |\n| - | - |\n| 1 | 2 |", "<table style="),
        ],
    )
    def test_every_block_element_carries_an_inline_style(self, markdown, tag):
        assert tag in markdown_to_outlook_html(markdown)

    def test_list_items_are_styled_too(self):
        html = markdown_to_outlook_html("- one\n- two")

        assert html.count("<li style=") == 2

    def test_table_cells_and_headers_are_styled(self):
        html = markdown_to_outlook_html("| a |\n| - |\n| 1 |")

        assert "<th style=" in html
        assert "<td style=" in html

    def test_the_duplicated_capital_margin_survives(self):
        """Outlook honours `Margin`, not `margin`. Dropping the duplicate looks
        like tidying and produces lists with no indentation."""
        html = markdown_to_outlook_html("- one")

        assert "margin:10px 0 10px 25px;Margin:10px 0 10px 25px;" in html

    def test_the_mso_bullet_hint_survives(self):
        assert "mso-special-format:bullet" in markdown_to_outlook_html("- one")

    def test_no_unstyled_block_tag_escapes(self):
        """The substitution table is exhaustive over the tags it names; a plain
        one left behind means a replacement stopped matching."""
        html = markdown_to_outlook_html("# H\n\n- a\n\n> q\n\n| a |\n| - |\n| 1 |\n")
        for plain, _ in OUTLOOK_STYLES:
            assert plain not in html, f"{plain} reached the body unstyled"

    def test_tables_need_the_extension_to_render_at_all(self):
        html = markdown_to_outlook_html("| a | b |\n| - | - |\n| 1 | 2 |")

        assert "<table" in html, "without the tables extension this is a paragraph of pipes"


class TestComposition:
    def test_the_signature_is_appended_after_two_breaks(self):
        assert compose_with_signature("<p>hi</p>", "<p>sig</p>") == "<p>hi</p><br><br><p>sig</p>"

    def test_an_empty_signature_leaves_the_body_untouched(self):
        """`include_signature: false`. A dangling separator would also leave a
        `cid:` reference with no attachment behind it."""
        assert compose_with_signature("<p>hi</p>", "") == "<p>hi</p>"

    def test_the_reply_merge_puts_the_quote_last(self):
        merged = merge_reply_body("<div>quoted</div>", "<p>mine</p>", "<p>sig</p>")

        assert merged == "<p>mine</p><br><br><p>sig</p><br><br><div>quoted</div>"

    def test_the_reply_merge_without_a_signature_still_keeps_the_quote(self):
        merged = merge_reply_body("<div>quoted</div>", "<p>mine</p>", "")

        assert merged == "<p>mine</p><br><br><div>quoted</div>"
