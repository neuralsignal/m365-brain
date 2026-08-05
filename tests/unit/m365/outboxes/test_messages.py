"""Graph draft operations: mailbox dispatch, the three-step reply, cc merge."""

from __future__ import annotations

import json

import pytest

from m365_brain.m365.outboxes.messages import (
    FORWARD,
    REPLY,
    REPLY_ALL,
    create_new_draft,
    create_reply_like,
    get_message,
    mailbox_base,
    merge_cc,
    recipient_list,
    update_draft,
)

from .conftest import QUOTED_ORIGINAL


class TestMailboxDispatch:
    @pytest.mark.parametrize("mailbox", ["me", ""])
    def test_the_personal_mailbox_routes_to_me(self, mailbox):
        assert mailbox_base(mailbox) == "/me"

    def test_any_other_value_is_a_upn(self):
        """A shared-mailbox draft has to land in the *shared* Drafts folder;
        sending it to /me produces a draft nobody looking there will find."""
        assert mailbox_base("shared@example.com") == "/users/shared@example.com"


class TestRecipients:
    def test_addresses_become_graphs_envelope(self):
        assert recipient_list(["a@example.com"]) == [{"emailAddress": {"address": "a@example.com"}}]

    def test_extra_cc_is_merged_not_replaced(self):
        existing = [{"emailAddress": {"address": "keep@example.com"}}]

        merged = merge_cc(existing, ["new@example.com"])

        assert [entry["emailAddress"]["address"] for entry in merged] == ["keep@example.com", "new@example.com"]

    def test_the_merge_dedups_case_insensitively(self):
        existing = [{"emailAddress": {"address": "Keep@Example.com"}}]

        merged = merge_cc(existing, ["keep@example.com", "new@example.com"])

        assert len(merged) == 2

    def test_no_existing_list_is_the_same_as_an_empty_one(self):
        assert merge_cc(None, ["a@example.com"]) == [{"emailAddress": {"address": "a@example.com"}}]


class TestCreateAndUpdate:
    def test_a_new_draft_posts_every_recipient_list(self, client, recorded):
        message_id = create_new_draft(
            client, "me", ["a@example.com"], ["c@example.com"], ["b@example.com"], "Subject", "<p>hi</p>", ""
        )

        assert message_id == "MSG-1"
        body = json.loads(recorded[0].content)
        assert body["subject"] == "Subject"
        assert body["body"] == {"contentType": "html", "content": "<p>hi</p>"}
        assert body["ccRecipients"] == [{"emailAddress": {"address": "c@example.com"}}]
        assert body["bccRecipients"] == [{"emailAddress": {"address": "b@example.com"}}]

    def test_the_signature_is_composed_into_the_posted_body(self, client, recorded):
        create_new_draft(client, "me", ["a@example.com"], [], [], "S", "<p>hi</p>", "<p>sig</p>")

        assert json.loads(recorded[0].content)["body"]["content"] == "<p>hi</p><br><br><p>sig</p>"

    def test_an_update_patches_the_same_id(self, client, recorded):
        returned = update_draft(client, "me", "MSG-EXISTING", ["a@example.com"], [], [], "New", "<p>new</p>", "")

        assert returned == "MSG-EXISTING"
        assert recorded[0].method == "PATCH"
        assert recorded[0].url.path.endswith("/messages/MSG-EXISTING")

    def test_create_and_update_build_the_same_payload_shape(self, client, recorded):
        create_new_draft(client, "me", ["a@example.com"], [], [], "S", "<p>x</p>", "<p>sig</p>")
        update_draft(client, "me", "MSG-1", ["a@example.com"], [], [], "S", "<p>x</p>", "<p>sig</p>")

        assert json.loads(recorded[0].content) == json.loads(recorded[1].content)


class TestThreeStepReply:
    def test_the_sequence_is_post_get_patch(self, client, recorded):
        create_reply_like(client, "me", "ORIG", REPLY, "<p>mine</p>", "", [], None)

        assert [request.method for request in recorded] == ["POST", "GET", "PATCH"]
        assert recorded[0].url.path.endswith("/messages/ORIG/createReply")
        assert recorded[1].url.path.endswith("/messages/NEW-1")

    def test_the_patch_carries_graphs_own_quoted_original(self, client, recorded):
        """The GET exists only to read this back. Skipping it drops the quote
        and the recipient sees a reply with no thread."""
        create_reply_like(client, "me", "ORIG", REPLY, "<p>mine</p>", "<p>sig</p>", [], None)

        content = json.loads(recorded[2].content)["body"]["content"]
        assert content == f"<p>mine</p><br><br><p>sig</p><br><br>{QUOTED_ORIGINAL}"

    def test_reply_all_uses_a_different_action(self, client, recorded):
        create_reply_like(client, "me", "ORIG", REPLY_ALL, "<p>x</p>", "", [], None)

        assert recorded[0].url.path.endswith("/createReplyAll")

    def test_forward_sets_to_recipients_at_the_top_level(self, client, recorded):
        create_reply_like(client, "me", "ORIG", FORWARD, "<p>x</p>", "", [], ["fwd@example.com"])

        patched = json.loads(recorded[2].content)
        assert patched["toRecipients"] == [{"emailAddress": {"address": "fwd@example.com"}}]
        assert "message" not in patched

    def test_extra_cc_merges_with_the_list_graph_derived(self, client, recorded):
        create_reply_like(client, "me", "ORIG", REPLY, "<p>x</p>", "", ["extra@example.com"], None)

        addresses = {entry["emailAddress"]["address"] for entry in json.loads(recorded[2].content)["ccRecipients"]}
        assert addresses == {"existing@example.com", "extra@example.com"}

    def test_the_shared_mailbox_base_is_used_for_all_three_calls(self, client, recorded):
        create_reply_like(client, "shared@example.com", "ORIG", REPLY, "<p>x</p>", "", [], None)

        assert all("/users/shared@example.com/" in str(request.url) for request in recorded)


class TestGetMessage:
    def test_a_select_list_is_spelled_into_the_path(self, client, recorded):
        get_message(client, "me", "MID", ["id", "isDraft"])

        assert "$select=id,isDraft" in str(recorded[0].url)

    def test_an_empty_select_adds_no_query(self, client, recorded):
        get_message(client, "me", "MID", [])

        assert "$select" not in str(recorded[0].url)

    def test_a_deleted_message_is_data_not_an_error(self, client, recorded):
        """It is how a rejection is detected, so 404 has to return None."""
        assert get_message(client, "me", "MSG-DELETED", ["id"]) is None
