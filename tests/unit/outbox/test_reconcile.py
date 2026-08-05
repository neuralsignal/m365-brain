"""The amendment heuristic and the four verdicts.

Ported from the classification half of a 507-line module; the two thirds of it
that projected outcomes into a knowledge base did not move and are the
consumer's.

The heuristic is the hardest-won logic in the source material and it has
**never run on production data** -- the workspace it came from has 61 body
snapshots and zero sent records, so the `sent` and `amended` branches were
exercised by nothing. These cases are therefore synthesised, and the offline
differential replay against the original implementation is what says the port
preserved the behaviour. See `docs/decisions/0015-amendment-path-unobserved.md`.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from m365_brain.outbox.reconcile import (
    RECONCILE_SELECT,
    QuoteMarkers,
    classify,
    detect_amended,
    html_to_text,
    markdown_to_text,
    normalize_whitespace,
    user_portion,
)
from m365_brain.vault.dispatch import DispatchReceipt

# The six the previous implementation compiled in. They live in config now;
# these are the values `m365-brain init` scaffolds so nobody invents regexes.
DEFAULT_MARKERS = [
    r"^\s*From:\s",
    r"^\s*Von:\s",
    r"^\s*On .+ wrote:\s*$",
    r"^\s*Am .+ schrieb:\s*$",
    r"^-{4,}\s*$",
    r"Mit freundlichen Gr[uü]ss?en",
]

ORIGINAL = "Hello Ada,\n\nThe report is attached.\n\nBest"


@pytest.fixture()
def markers():
    return QuoteMarkers.from_config(DEFAULT_MARKERS)


def _receipt(message_id: str | None = "MSG-1") -> DispatchReceipt:
    return DispatchReceipt(
        uuid="abc",
        kind="email.draft",
        outcome="dispatched",
        dispatched_at=datetime(2026, 8, 5, 9, 0, tzinfo=UTC),
        graph_message_id=message_id,
        reason=None,
        detail=None,
    )


class TestMarkerCompilation:
    def test_a_bad_regex_crashes_at_load_naming_the_pattern(self):
        with pytest.raises(ValueError) as excinfo:
            QuoteMarkers.from_config(["(unclosed"])

        assert "(unclosed" in str(excinfo.value)

    def test_an_empty_table_is_legitimate(
        self,
    ):
        """A deployment with no quoting convention is a valid deployment; the
        table is tenant policy, not a package constant."""
        assert user_portion("From: someone", QuoteMarkers.from_config([])) == "From: someone"


class TestTextFlattening:
    def test_whitespace_collapses(self):
        assert normalize_whitespace("a  \n\n b\t c ") == "a b c"

    def test_html_becomes_line_separated_text(self):
        assert "one" in html_to_text("<p>one</p><p>two</p>")

    def test_markdown_goes_through_its_rendered_html(self):
        """Via HTML rather than by stripping syntax, so the comparison sees
        what the recipient saw."""
        text = markdown_to_text("| a | b |\n| - | - |\n| 1 | 2 |")
        assert "1" in text and "|" not in text


class TestUserPortion:
    @pytest.mark.parametrize(
        "marker_line",
        [
            "From: sender@example.com",
            "Von: sender@example.com",
            "On Monday someone wrote:",
            "Am 5. August 2026 schrieb:",
            "--------",
            "Mit freundlichen Grüssen",
        ],
    )
    def test_every_configured_marker_truncates(self, markers, marker_line):
        text = f"my reply\n{marker_line}\nquoted original"

        assert user_portion(text, markers).strip() == "my reply"

    def test_the_earliest_marker_wins(self, markers):
        text = "my reply\nFrom: a\nmiddle\nVon: b\ntail"

        assert user_portion(text, markers).strip() == "my reply"

    def test_text_with_no_marker_survives_whole(self, markers):
        assert user_portion("just text", markers) == "just text"


class TestDetectAmended:
    def test_an_identical_body_is_not_amended(self, markers):
        assert detect_amended(ORIGINAL, f"<p>{ORIGINAL}</p>", markers) is False

    def test_a_quoted_original_after_the_body_is_not_amended(self, markers):
        sent = f"<p>{ORIGINAL}</p><br><br><div>From: someone@example.com<br>the whole earlier thread</div>"

        assert detect_amended(ORIGINAL, sent, markers) is False

    def test_appending_to_the_body_is_not_amended(self, markers):
        """Containment counts as unamended: adding a line leaves the model's
        own text intact, which is what this measures."""
        sent = f"<p>{ORIGINAL} PS: see you Thursday.</p>"

        assert detect_amended(ORIGINAL, sent, markers) is False

    def test_a_rewritten_body_is_amended(self, markers):
        assert detect_amended(ORIGINAL, "<p>Completely different wording.</p>", markers) is True

    def test_prefixing_the_body_is_not_amended_either(self, markers):
        """Containment is position-independent, so a preamble reads the same as
        a postscript. Coarse by design -- a reviewer does the real diff."""
        assert detect_amended(ORIGINAL, f"<p>Actually, {ORIGINAL}</p>", markers) is False

    def test_two_empty_bodies_are_not_amended(self, markers):
        assert detect_amended("", "", markers) is False

    def test_an_emptied_body_is_amended(self, markers):
        assert detect_amended(ORIGINAL, "<p></p>", markers) is True


class TestClassify:
    def test_a_missing_message_is_a_rejection(self, markers):
        outcome = classify(_receipt(), None, ORIGINAL, markers)

        assert outcome.verdict == "rejected"
        assert outcome.graph_message_id == "MSG-1"
        assert outcome.sent_body_html == ""
        assert outcome.original_body == ORIGINAL

    def test_a_still_open_draft_is_pending(self, markers):
        outcome = classify(_receipt(), {"isDraft": True, "body": {"content": "<p>x</p>"}}, ORIGINAL, markers)

        assert outcome.verdict == "pending"

    def test_an_untouched_sent_message_is_sent(self, markers):
        item = {
            "isDraft": False,
            "body": {"content": f"<p>{ORIGINAL}</p>"},
            "conversationId": "CONV-1",
            "sentDateTime": "2026-08-05T10:00:00Z",
        }

        outcome = classify(_receipt(), item, ORIGINAL, markers)

        assert outcome.verdict == "sent"
        assert outcome.conversation_id == "CONV-1"
        assert outcome.sent_at == "2026-08-05T10:00:00Z"

    def test_an_edited_sent_message_is_amended_not_sent(self, markers):
        """A fourth verdict, not a boolean on `sent`. The counters this
        replaces treated it as a subset and double-counted every one."""
        item = {"isDraft": False, "body": {"content": "<p>Rewritten entirely.</p>"}}

        assert classify(_receipt(), item, ORIGINAL, markers).verdict == "amended"

    def test_the_outcome_carries_content_not_paths(self, markers):
        """No knowledge-base path crosses the boundary in either direction."""
        item = {"isDraft": False, "body": {"content": "<p>text</p>"}}

        outcome = classify(_receipt(), item, ORIGINAL, markers)

        assert outcome.sent_body_html == "<p>text</p>"
        assert outcome.original_body == ORIGINAL
        assert not any("path" in field for field in type(outcome).model_fields)

    def test_classify_is_pure(self, markers):
        """No Graph call and no filesystem: it is what makes an offline replay
        of a whole corpus possible."""
        item = {"isDraft": False, "body": {"content": "<p>text</p>"}}

        first = classify(_receipt(), item, ORIGINAL, markers)
        second = classify(_receipt(), item, ORIGINAL, markers)

        assert first == second


def test_the_select_list_is_complete():
    """A dropped field does not fail -- it degrades classification silently."""
    assert set(RECONCILE_SELECT) == {"id", "isDraft", "subject", "body", "conversationId", "sentDateTime"}
