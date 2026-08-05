"""Every path comes from config, and none of them escapes the vault.

The fixture layout renames everything (see conftest), so a hardcoded `inbox`,
`index.md` or `attachments` anywhere in the resolver produces a visibly wrong
string here rather than a coincidentally right one.

The hypothesis properties cover the part a table of examples cannot: whatever
segments a caller passes, the result must stay inside the vault, stay
addressable by a storage backend, and never grow a doubled slash.
"""

from __future__ import annotations

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from m365_brain.vault.paths import VaultPathError, VaultPaths

# Segments a caller could plausibly build from Graph data: slugs, hashes,
# dates, unicode display names. Separators and traversal are tested separately
# as rejections, so they are excluded here.
SAFE_SEGMENTS = st.text(
    alphabet=st.characters(blacklist_characters="/\\", blacklist_categories=("Cs", "Cc")),
    min_size=1,
    max_size=20,
).filter(lambda s: s != "..")


# The vault fixtures are frozen Pydantic models and a frozen dataclass, so
# hypothesis not resetting them between generated inputs is harmless -- which
# is exactly the case this health check exists to let you opt out of.
IMMUTABLE_FIXTURES = settings(suppress_health_check=[HealthCheck.function_scoped_fixture])


class TestInbox:
    def test_inbox_root_uses_the_configured_names(self, paths):
        assert paths.inbox_root("email") == "incoming/mail"
        assert paths.inbox_root("teams_chats") == "incoming/chats"

    def test_inbox_item_appends_segments(self, paths):
        assert paths.inbox_item("email", "2026", "2026-08-05", "subject-ab12cd") == (
            "incoming/mail/2026/2026-08-05/subject-ab12cd"
        )

    def test_unknown_extractor_names_the_config_key_and_the_known_names(self, paths):
        with pytest.raises(VaultPathError) as excinfo:
            paths.inbox_root("slack")
        message = str(excinfo.value)
        assert "vault.extractor_dirs" in message
        assert "'slack'" in message
        assert "email" in message, "the message must list what IS configured, not just what is not"

    def test_extractor_dir_is_not_derived_from_the_extractor_name(self, paths):
        """`email` -> `mail` here, `emails` conventionally. Neither is mechanical."""
        assert paths.extractor_dir("email") != "email"
        assert paths.extractor_dir("teams_chats") == "chats"


class TestItemFiles:
    def test_entry_conversation_and_store_filenames_come_from_config(self, paths):
        item = paths.inbox_item("email", "x")
        assert paths.entry_file(item) == "incoming/mail/x/page.md"
        assert paths.conversation_file(item) == "incoming/mail/x/thread.md"
        assert paths.conversation_store(item) == "incoming/mail/x/thread.ndjson"

    def test_attachment_paths_use_the_configured_directory_names(self, paths):
        item = paths.inbox_item("teams_chats", "chat_ab12cd")
        assert paths.attachment(item, "msg-1", "report.pdf") == "incoming/chats/chat_ab12cd/files/msg-1/report.pdf"
        assert paths.converted_attachment(item, "msg-1", "report.pdf.md") == (
            "incoming/chats/chat_ab12cd/files-as-text/msg-1/report.pdf.md"
        )

    def test_empty_item_dir_yields_the_item_relative_form(self, paths):
        """The Teams renderer links relative to messages.md, so it needs this form.

        It must come from the same method as the absolute one — two
        implementations of the same path is exactly how a link goes stale.
        """
        assert paths.attachment("", "msg-1", "report.pdf") == "files/msg-1/report.pdf"
        assert paths.converted_attachment("", "msg-1", "r.md") == "files-as-text/msg-1/r.md"

    def test_the_relative_form_is_a_suffix_of_the_absolute_one(self, paths):
        item = paths.inbox_item("teams_chats", "chat_ab12cd")
        absolute = paths.attachment(item, "m", "f.pdf")
        relative = paths.attachment("", "m", "f.pdf")
        assert absolute == f"{item}/{relative}"


class TestOutboxAndMeta:
    def test_outbox_paths_use_the_configured_root(self, paths):
        assert paths.outbox("email.draft") == "pending/email.draft"
        assert paths.outbox_intent("email.draft", "abc-123") == "pending/email.draft/abc-123.md"

    def test_archive_paths_live_under_meta(self, paths):
        assert paths.inflight("abc") == "dot-meta/claimed/abc.md"
        assert paths.processed("abc") == "dot-meta/done/abc.md"
        assert paths.rejected("abc") == "dot-meta/refused/abc.md"

    def test_the_receipt_is_a_sidecar_beside_the_intent_in_either_archive(self, paths):
        """A sidecar, not injected frontmatter: the archived intent must still
        parse under its own `extra="forbid"` when re-read. Both archives get
        one, because a rejection has to say why as much as a dispatch does."""
        assert paths.processed_receipt("abc") == "dot-meta/done/abc.receipt.json"
        assert paths.rejected_receipt("abc") == "dot-meta/refused/abc.receipt.json"
        assert paths.processed_receipt("abc") != paths.processed("abc")
        assert paths.rejected_receipt("abc") != paths.rejected("abc")

    def test_state_and_manifests_sit_under_meta(self, paths):
        assert paths.state("sync.json") == "dot-meta/cursors/sync.json"
        assert paths.manifests("2026-08-05.json") == "dot-meta/runs/2026-08-05.json"

    def test_annotations_are_a_sibling_tree_not_a_child_of_inbox(self, paths):
        annotation = paths.annotations("mail", "x.md")
        assert annotation == "notes/mail/x.md"
        assert not annotation.startswith("incoming/")


class TestRejections:
    @pytest.mark.parametrize(
        "segment",
        ["..", "a/../../etc", "../escape"],
    )
    def test_parent_traversal_is_refused(self, paths, segment):
        with pytest.raises(VaultPathError, match="traversal"):
            paths.inbox_item("email", segment)

    def test_a_leading_separator_is_refused(self, paths):
        with pytest.raises(VaultPathError, match="separator"):
            paths.inbox_item("email", "/absolute")

    def test_an_empty_segment_inside_a_segment_is_refused(self, paths):
        """`a//b` addresses nothing on either backend, so it fails loudly."""
        with pytest.raises(VaultPathError, match="empty"):
            paths.inbox_item("email", "a//b")

    def test_backslashes_are_normalised_not_rejected(self, paths):
        """A Windows-shaped segment is a caller mistake with an obvious intent."""
        assert paths.inbox_item("email", "a\\b") == "incoming/mail/a/b"

    def test_a_traversal_hidden_behind_a_backslash_is_still_refused(self, paths):
        with pytest.raises(VaultPathError, match="traversal"):
            paths.inbox_item("email", "a\\..\\..\\etc")


class TestProperties:
    @IMMUTABLE_FIXTURES
    @given(segments=st.lists(SAFE_SEGMENTS, min_size=1, max_size=4))
    def test_an_inbox_item_never_escapes_its_extractor_root(self, vault_config, segments):
        paths = VaultPaths(vault_config)
        try:
            result = paths.inbox_item("email", *segments)
        except VaultPathError:
            return  # a rejected segment is a valid outcome; escaping is not
        assert result.startswith(paths.inbox_root("email") + "/")

    @IMMUTABLE_FIXTURES
    @given(segments=st.lists(SAFE_SEGMENTS, min_size=1, max_size=4))
    def test_a_built_path_is_always_backend_addressable(self, vault_config, segments):
        """Relative, forward-slashed, no empty component. That is the whole
        contract `StorageBackend` relies on."""
        paths = VaultPaths(vault_config)
        try:
            result = paths.inbox_item("email", *segments)
        except VaultPathError:
            return
        assert not result.startswith("/")
        assert "//" not in result
        assert "\\" not in result
        assert all(part for part in result.split("/"))

    @IMMUTABLE_FIXTURES
    @given(segments=st.lists(SAFE_SEGMENTS, min_size=1, max_size=3))
    def test_no_segment_is_silently_dropped(self, vault_config, segments):
        paths = VaultPaths(vault_config)
        try:
            result = paths.inbox_item("email", *segments)
        except VaultPathError:
            return
        # 2 for the inbox root, plus one component per segment (segments may
        # themselves contain no separator, by construction of the strategy).
        assert len(result.split("/")) == 2 + len(segments)
