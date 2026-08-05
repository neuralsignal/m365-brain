"""Verdicts on untrusted outbox paths.

Two properties carry the weight. First, the classifier never raises — it judges
input that arrived from somewhere else, and a caller that has to wrap it in a
try/except to get a verdict back would just be reimplementing it. Second, the
archive segment names come from config: a hardcoded `_processed` would stop
skipping the archive the moment an operator renamed it, and the runner would
re-dispatch every intent it had ever sent.

The fixture layout renames every segment, so any literal in the implementation
shows up as a wrong verdict here.
"""

from __future__ import annotations

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from m365_brain.vault.classify import ClassifiedPath, PathClassification, classify_outbox_path


def classify(path: str, layout) -> ClassifiedPath:
    return classify_outbox_path(path, layout)


# The vault fixtures are frozen Pydantic models and a frozen dataclass, so
# hypothesis not resetting them between generated inputs is harmless -- which
# is exactly the case this health check exists to let you opt out of.
IMMUTABLE_FIXTURES = settings(suppress_health_check=[HealthCheck.function_scoped_fixture])


class TestValid:
    def test_a_well_formed_intent_path_yields_its_outbox_and_uuid(self, layout):
        result = classify("pending/email.draft/abc-123.md", layout)
        assert result.classification is PathClassification.VALID
        assert result.outbox_name == "email.draft"
        assert result.uuid == "abc-123"
        assert result.reason is None

    def test_the_uuid_is_the_filename_stem(self, layout):
        """The stem IS the identity — the runner cross-checks it against the
        envelope, so a resolver that returned the whole filename would make
        every intent look like a mismatch."""
        assert classify("pending/teams.post/9f8e.md", layout).uuid == "9f8e"

    def test_the_outbox_root_comes_from_config(self, layout):
        """`outbox` is the conventional name; this layout calls it `pending`."""
        assert classify("outbox/email.draft/a.md", layout).classification is PathClassification.REJECT
        assert classify("pending/email.draft/a.md", layout).classification is PathClassification.VALID


class TestSkip:
    @pytest.mark.parametrize("segment", ["done", "refused", "claimed"])
    def test_every_configured_archive_segment_is_skipped(self, layout, segment):
        result = classify(f"pending/{segment}/abc.md", layout)
        assert result.classification is PathClassification.SKIP
        assert result.reason is None

    def test_the_conventional_archive_names_are_not_special_under_this_layout(self, layout):
        """`_processed` means nothing here; treating it as an archive would be a
        hardcoded name surviving in the classifier."""
        assert classify("pending/_processed/abc.md", layout).classification is PathClassification.VALID

    def test_an_archive_segment_deeper_in_the_path_still_skips(self, layout):
        assert classify("pending/email.draft/done/abc.md", layout).classification is PathClassification.SKIP


class TestReject:
    @pytest.mark.parametrize(
        ("path", "fragment"),
        [
            ("", "empty"),
            ("/pending/email.draft/a.md", "absolute"),
            ("\\pending\\email.draft\\a.md", "absolute"),
            ("pending/../../etc/passwd", "traversal"),
            ("knowledge/email.draft/a.md", "not under"),
            ("pending", "layout"),
            ("pending/email.draft", "layout"),
            ("pending/a/b/c.md", "layout"),
            ("pending/email.draft/a.txt", ".md"),
            ("pending/email.draft/.md", "empty"),
        ],
    )
    def test_each_rejection_carries_a_reason_naming_what_was_wrong(self, layout, path, fragment):
        result = classify(path, layout)
        assert result.classification is PathClassification.REJECT
        assert result.outbox_name is None
        assert result.uuid is None
        assert fragment in result.reason

    def test_a_non_string_is_rejected_rather_than_crashing(self, layout):
        """The caller feeds it a storage listing; a bad entry is data, not a bug."""
        assert classify_outbox_path(None, layout).classification is PathClassification.REJECT


class TestProperties:
    @IMMUTABLE_FIXTURES
    @given(path=st.text(max_size=60))
    def test_it_never_raises_on_any_input(self, layout, path):
        result = classify_outbox_path(path, layout)
        assert result.classification in set(PathClassification)

    @IMMUTABLE_FIXTURES
    @given(path=st.text(max_size=60))
    def test_outbox_name_and_uuid_are_set_exactly_when_valid(self, layout, path):
        result = classify_outbox_path(path, layout)
        populated = result.outbox_name is not None and result.uuid is not None
        assert populated == (result.classification is PathClassification.VALID)

    @IMMUTABLE_FIXTURES
    @given(path=st.text(max_size=60))
    def test_a_reason_is_present_exactly_when_rejected(self, layout, path):
        result = classify_outbox_path(path, layout)
        assert (result.reason is not None) == (result.classification is PathClassification.REJECT)

    @IMMUTABLE_FIXTURES
    @given(
        outbox_name=st.text(alphabet=st.characters(whitelist_categories=("Ll", "Nd")), min_size=1, max_size=12),
        uuid=st.text(alphabet=st.characters(whitelist_categories=("Ll", "Nd")), min_size=1, max_size=12),
    )
    def test_a_path_built_by_the_resolver_always_classifies_as_valid(self, layout, paths, outbox_name, uuid):
        """The round trip that matters: what `VaultPaths.outbox_intent` writes,
        `classify_outbox_path` must admit."""
        result = classify_outbox_path(paths.outbox_intent(outbox_name, uuid), layout)
        assert result.classification is PathClassification.VALID
        assert result.outbox_name == outbox_name
        assert result.uuid == uuid
