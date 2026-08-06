"""Tests for email extractor."""

from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest
import respx
from pytest_httpx import HTTPXMock

from m365_brain.config import EmailExtractorConfig, GraphConfig, MailboxConfig
from m365_brain.m365.client import GraphApiError, GraphClient
from m365_brain.m365.extractors import _attachment_helpers, _folder_helpers, email
from m365_brain.storage.local import LocalBackend
from m365_brain.vault.removal import PATH_MAP_STATE_KEY

FIXTURES_DIR = Path(__file__).resolve().parents[3] / "fixtures"


@pytest.fixture()
def email_config():
    return EmailExtractorConfig(
        enabled=True,
        poll_interval_minutes=3,
        mailboxes=[
            MailboxConfig(address="me", folders=["Inbox"], output_subdir=""),
        ],
        max_items_per_sync=100,
        download_attachments=False,
        max_attachment_size_mb=25,
        attachment_convert_extensions=[],
    )


@pytest.fixture()
def graph_config():
    return GraphConfig(
        max_retries=1,
        backoff_base_ms=10,
        timeout_seconds=5,
        max_pages=10,
        max_retry_after_seconds=300.0,
        error_message_max_length=200,
    )


@pytest.fixture()
def email_response():
    return json.loads((FIXTURES_DIR / "email_response.json").read_text())


class TestEmailExtractor:
    def test_sync_produces_markdown(
        self, httpx_mock: HTTPXMock, tmp_path, graph_config, email_config, email_response, ctx
    ):
        httpx_mock.add_response(
            url=re.compile(r".*/me/mailFolders/Inbox/messages/delta.*"),
            json=email_response,
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        state, count = email.run(client, storage, {}, email_config, ctx)

        assert count == 2
        assert "delta_link_me_Inbox" in state
        assert "last_sync" in state

        files = storage.list_files(ctx.paths.inbox_root("email"))
        assert len(files) == 2

        client.close()

    def test_incremental_sync_uses_delta_link(self, httpx_mock: HTTPXMock, tmp_path, graph_config, email_config, ctx):
        delta_url = "https://graph.microsoft.com/v1.0/me/mailFolders/Inbox/messages/delta?$deltatoken=existing"
        httpx_mock.add_response(
            url=delta_url,
            json={
                "value": [
                    {
                        "id": "new-msg-1",
                        "subject": "New email",
                        "body": {"contentType": "text", "content": "Hello"},
                        "from": {"emailAddress": {"name": "Test", "address": "test@example.com"}},
                        "toRecipients": [],
                        "receivedDateTime": "2026-03-12T15:00:00Z",
                        "importance": "normal",
                        "hasAttachments": False,
                        "webLink": "",
                        "parentFolderId": "inbox",
                    }
                ],
                "@odata.deltaLink": "https://graph.microsoft.com/v1.0/delta?token=new",
            },
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        existing_state = {"delta_link_me_Inbox": delta_url}
        state, count = email.run(client, storage, existing_state, email_config, ctx)

        assert count == 1
        assert state["delta_link_me_Inbox"] == "https://graph.microsoft.com/v1.0/delta?token=new"
        client.close()

    def test_empty_response(self, httpx_mock: HTTPXMock, tmp_path, graph_config, email_config, ctx):
        httpx_mock.add_response(
            url=re.compile(r".*/me/mailFolders/Inbox/messages/delta.*"),
            json={"value": [], "@odata.deltaLink": "https://delta?token=empty"},
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        state, count = email.run(client, storage, {}, email_config, ctx)
        assert count == 0
        client.close()

    def test_html_body_converted_to_markdown(
        self, httpx_mock: HTTPXMock, tmp_path, graph_config, email_config, email_response, ctx
    ):
        httpx_mock.add_response(
            url=re.compile(r".*/me/mailFolders/Inbox/messages/delta.*"),
            json=email_response,
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        email.run(client, storage, {}, email_config, ctx)

        files = storage.list_files(ctx.paths.inbox_root("email"))
        all_content = "\n".join(storage.read_file(f) for f in files)

        # HTML should be converted — no raw <html> tags
        assert "<html>" not in all_content
        # Content from the HTML email should be present as markdown
        assert "budget" in all_content.lower()
        client.close()

    def test_multiple_folders(self, httpx_mock: HTTPXMock, tmp_path, graph_config, ctx):
        config = EmailExtractorConfig(
            enabled=True,
            poll_interval_minutes=3,
            mailboxes=[
                MailboxConfig(address="me", folders=["Inbox", "SentItems"], output_subdir=""),
            ],
            max_items_per_sync=100,
            download_attachments=False,
            max_attachment_size_mb=25,
            attachment_convert_extensions=[],
        )

        for folder in ["Inbox", "SentItems"]:
            httpx_mock.add_response(
                url=re.compile(rf".*/me/mailFolders/{folder}/messages/delta.*"),
                json={
                    "value": [
                        {
                            "id": f"msg-{folder}",
                            "subject": f"Email in {folder}",
                            "body": {"contentType": "text", "content": "Body"},
                            "from": {"emailAddress": {"name": "Test", "address": "test@example.com"}},
                            "toRecipients": [],
                            "receivedDateTime": "2026-03-12T10:00:00Z",
                            "importance": "normal",
                            "hasAttachments": False,
                            "webLink": "",
                            "parentFolderId": folder,
                        }
                    ],
                    "@odata.deltaLink": f"https://delta?token={folder}",
                },
            )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        state, count = email.run(client, storage, {}, config, ctx)
        assert count == 2
        assert "delta_link_me_Inbox" in state
        assert "delta_link_me_SentItems" in state
        client.close()

    def test_initial_sync_logs_sync_type(self, httpx_mock: HTTPXMock, tmp_path, graph_config, email_config, ctx):
        """Initial sync (no delta_link) logs sync_type='initial'."""
        httpx_mock.add_response(
            url=re.compile(r".*/me/mailFolders/Inbox/messages/delta.*"),
            json={"value": [], "@odata.deltaLink": "https://delta?token=init"},
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        events: list[dict] = []

        def capture_log(event, **kwargs):
            events.append({"event": event, **kwargs})

        with patch.object(email.log, "info", side_effect=capture_log):
            email.run(client, storage, {}, email_config, ctx)

        sync_start_events = [e for e in events if e["event"] == "email.folder_sync_start"]
        assert len(sync_start_events) == 1
        assert sync_start_events[0]["sync_type"] == "initial"
        client.close()

    def test_incremental_sync_logs_sync_type(self, httpx_mock: HTTPXMock, tmp_path, graph_config, email_config, ctx):
        """Incremental sync (with delta_link) logs sync_type='incremental'."""
        delta_url = "https://graph.microsoft.com/v1.0/me/mailFolders/Inbox/messages/delta?$deltatoken=existing"
        httpx_mock.add_response(
            url=delta_url,
            json={"value": [], "@odata.deltaLink": "https://delta?token=new"},
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        events: list[dict] = []

        def capture_log(event, **kwargs):
            events.append({"event": event, **kwargs})

        with patch.object(email.log, "info", side_effect=capture_log):
            email.run(client, storage, {"delta_link_me_Inbox": delta_url}, email_config, ctx)

        sync_start_events = [e for e in events if e["event"] == "email.folder_sync_start"]
        assert len(sync_start_events) == 1
        assert sync_start_events[0]["sync_type"] == "incremental"
        client.close()

    def test_very_long_subject(self, httpx_mock: HTTPXMock, tmp_path, graph_config, email_config, ctx):
        """500-char subject should be slugified and truncated to a valid file path."""
        long_subject = "A" * 500
        httpx_mock.add_response(
            url=re.compile(r".*/me/mailFolders/Inbox/messages/delta.*"),
            json={
                "value": [
                    {
                        "id": "msg-long-subj",
                        "subject": long_subject,
                        "body": {"contentType": "text", "content": "Body"},
                        "from": {"emailAddress": {"name": "Test", "address": "test@example.com"}},
                        "toRecipients": [],
                        "receivedDateTime": "2026-03-12T10:00:00Z",
                        "importance": "normal",
                        "hasAttachments": False,
                        "webLink": "",
                        "parentFolderId": "inbox",
                    }
                ],
                "@odata.deltaLink": "https://delta?token=long",
            },
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        state, count = email.run(client, storage, {}, email_config, ctx)
        assert count == 1

        files = storage.list_files(ctx.paths.inbox_root("email"))
        assert len(files) == 1
        # File path slug should be truncated (max_length=80 default for slugify)
        assert len(files[0]) < 200
        client.close()

    def test_subject_with_yaml_special_chars(self, httpx_mock: HTTPXMock, tmp_path, graph_config, email_config, ctx):
        """Subject with YAML special characters must not corrupt frontmatter."""
        tricky_subject = 'RE: FW: Budget (Q1) — Final #2 [v3]: "approved"'
        httpx_mock.add_response(
            url=re.compile(r".*/me/mailFolders/Inbox/messages/delta.*"),
            json={
                "value": [
                    {
                        "id": "msg-yaml-chars",
                        "subject": tricky_subject,
                        "body": {"contentType": "text", "content": "Approved."},
                        "from": {"emailAddress": {"name": "Boss", "address": "boss@example.com"}},
                        "toRecipients": [{"emailAddress": {"address": "me@example.com"}}],
                        "receivedDateTime": "2026-03-12T14:00:00Z",
                        "importance": "high",
                        "hasAttachments": False,
                        "webLink": "",
                        "parentFolderId": "inbox",
                    }
                ],
                "@odata.deltaLink": "https://delta?token=yaml",
            },
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        email.run(client, storage, {}, email_config, ctx)

        files = storage.list_files(ctx.paths.inbox_root("email"))
        content = storage.read_file(files[0])

        from m365_brain.m365.markdown_writer import loads_markdown

        fm, body = loads_markdown(content)
        assert fm["title"] == tricky_subject
        client.close()

    def test_missing_sender_fields(self, httpx_mock: HTTPXMock, tmp_path, graph_config, email_config, ctx):
        """Email with null from field should still be written with empty sender."""
        httpx_mock.add_response(
            url=re.compile(r".*/me/mailFolders/Inbox/messages/delta.*"),
            json={
                "value": [
                    {
                        "id": "msg-no-sender",
                        "subject": "System notification",
                        "body": {"contentType": "text", "content": "Alert"},
                        "from": None,
                        "toRecipients": [],
                        "receivedDateTime": "2026-03-12T08:00:00Z",
                        "importance": "normal",
                        "hasAttachments": False,
                        "webLink": "",
                        "parentFolderId": "inbox",
                    }
                ],
                "@odata.deltaLink": "https://delta?token=nosender",
            },
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        state, count = email.run(client, storage, {}, email_config, ctx)
        assert count == 1

        files = storage.list_files(ctx.paths.inbox_root("email"))
        content = storage.read_file(files[0])
        assert "System notification" in content
        client.close()

    def test_skips_invalid_messages(self, httpx_mock: HTTPXMock, tmp_path, graph_config, email_config, ctx):
        httpx_mock.add_response(
            url=re.compile(r".*/me/mailFolders/Inbox/messages/delta.*"),
            json={
                "value": [
                    {"id": "", "subject": "No ID", "receivedDateTime": "2026-03-12T10:00:00Z"},
                    {"id": "valid-id", "subject": "No Date"},
                ],
                "@odata.deltaLink": "https://delta?token=skip",
            },
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        state, count = email.run(client, storage, {}, email_config, ctx)
        assert count == 0
        client.close()


# ---------------------------------------------------------------------------
# Removal
# ---------------------------------------------------------------------------


class TestEmailRemoval:
    """An `@removed` marker deletes the file the earlier cycle wrote."""

    _LIVE = {
        "id": "msg-doomed",
        "subject": "Delete me",
        "body": {"contentType": "text", "content": "body"},
        "from": {"emailAddress": {"name": "T", "address": "t@example.com"}},
        "toRecipients": [],
        "receivedDateTime": "2026-03-12T10:00:00Z",
        "importance": "normal",
        "hasAttachments": False,
        "webLink": "",
        "parentFolderId": "inbox",
    }
    _REMOVED = {"id": "msg-doomed", "@removed": {"reason": "deleted"}}

    def test_removed_marker_deletes_file_and_map_entry_idempotently(
        self, httpx_mock: HTTPXMock, local_storage, graph_config, email_config, ctx
    ):
        # Cycle 1 — the message arrives and is written.
        httpx_mock.add_response(
            url=re.compile(r".*/me/mailFolders/Inbox/messages/delta.*"),
            json={"value": [self._LIVE], "@odata.deltaLink": "https://graph.microsoft.com/v1.0/delta?token=c1"},
        )
        client = GraphClient(graph_config, lambda: "test-token")

        state, count = email.run(client, local_storage, {}, email_config, ctx)

        assert count == 1
        written = state[PATH_MAP_STATE_KEY]["msg-doomed"]
        assert local_storage.file_exists(written)

        # Cycle 2 — the same id comes back as @removed.
        httpx_mock.add_response(
            url="https://graph.microsoft.com/v1.0/delta?token=c1",
            json={"value": [self._REMOVED], "@odata.deltaLink": "https://graph.microsoft.com/v1.0/delta?token=c2"},
        )
        state, count = email.run(client, local_storage, state, email_config, ctx)

        assert count == 0
        assert not local_storage.file_exists(written)
        assert "msg-doomed" not in state[PATH_MAP_STATE_KEY]

        # Cycle 3 — upstream re-sends the marker. delete_file is contractually
        # idempotent, so a second pass is a clean no-op rather than a 404.
        httpx_mock.add_response(
            url="https://graph.microsoft.com/v1.0/delta?token=c2",
            json={"value": [self._REMOVED], "@odata.deltaLink": "https://graph.microsoft.com/v1.0/delta?token=c3"},
        )
        state, count = email.run(client, local_storage, state, email_config, ctx)

        assert count == 0
        assert local_storage.list_files(ctx.paths.inbox_root("email")) == []
        client.close()


# ---------------------------------------------------------------------------
# Golden path under a non-default vault layout
# ---------------------------------------------------------------------------


class TestOddVaultLayout:
    """The recorded fixture, written into a vault where nothing is named conventionally.

    Asserted against `odd_ctx` on purpose: the same golden assertion made with
    the conventional names would still pass if the extractor had the directory
    and filename hardcoded, which is the regression this exists to catch.
    """

    def test_entry_lands_under_configured_dir_and_filename(
        self, httpx_mock: HTTPXMock, local_storage, graph_config, email_config, email_response, odd_ctx, vault_paths
    ):
        httpx_mock.add_response(
            url=re.compile(r".*/me/mailFolders/Inbox/messages/delta.*"),
            json=email_response,
        )
        client = GraphClient(graph_config, lambda: "test-token")

        _, count = email.run(client, local_storage, {}, email_config, odd_ctx)

        assert count == 2

        # `mail`, not `emails`; `page.md`, not `index.md`. Hashes are
        # sha256(message id)[:6] over the two ids in the fixture.
        root = odd_ctx.paths.inbox_root("email")
        assert local_storage.list_files(root) == [
            f"{root}/2026/2026-03-12/lunch-tomorrow-b6399c/page.md",
            f"{root}/2026/2026-03-12/q1-budget-review-545b96/page.md",
        ]
        # And nothing at all under the conventional layout.
        assert local_storage.list_files(vault_paths.inbox_root("email")) == []
        client.close()


class TestDeltaPageBudget:
    """B2: the page walk is bounded by graph.max_pages and nothing fetched is sliced away.

    The *item* budget is `$top` (see `TestDeltaTopCarriesTheItemBudget`); these
    tests cover the other bound — a round the page cap interrupts must resume,
    not restart, and must never drop what it already fetched.
    """

    @staticmethod
    def _msg(msg_id: str, subject: str, received: str) -> dict:
        return {
            "id": msg_id,
            "subject": subject,
            "body": {"contentType": "text", "content": "body"},
            "from": {"emailAddress": {"name": "Test", "address": "test@example.com"}},
            "toRecipients": [],
            "receivedDateTime": received,
            "importance": "normal",
            "hasAttachments": False,
            "webLink": "",
            "parentFolderId": "inbox",
        }

    @staticmethod
    def _small_config() -> EmailExtractorConfig:
        return EmailExtractorConfig(
            enabled=True,
            poll_interval_minutes=3,
            mailboxes=[MailboxConfig(address="me", folders=["Inbox"], output_subdir="")],
            max_items_per_sync=2,
            download_attachments=False,
            max_attachment_size_mb=25,
            attachment_convert_extensions=[],
        )

    def test_everything_fetched_is_processed_no_post_hoc_slice(
        self, httpx_mock: HTTPXMock, tmp_path, graph_config, ctx
    ):
        """A page can exceed max_items_per_sync; every fetched message must still be written."""
        httpx_mock.add_response(
            url=re.compile(r".*/me/mailFolders/Inbox/messages/delta.*"),
            json={
                "value": [
                    self._msg("m1", "First", "2026-03-12T15:01:00Z"),
                    self._msg("m2", "Second", "2026-03-12T15:02:00Z"),
                    self._msg("m3", "Third", "2026-03-12T15:03:00Z"),
                ],
                "@odata.deltaLink": "https://graph.microsoft.com/delta?token=done",
            },
        )
        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        state, count = email.run(client, storage, {}, self._small_config(), ctx)

        assert count == 3
        assert len(storage.list_files(ctx.paths.inbox_root("email"))) == 3
        client.close()

    def test_capped_fetch_resumes_from_pending_next_link(self, httpx_mock: HTTPXMock, tmp_path, graph_config, ctx):
        """Regression: a delta round capped mid-way resumes next cycle; the tail is never skipped."""
        pending = "https://graph.microsoft.com/v1.0/me/mailFolders/Inbox/messages/delta?$skiptoken=tail"
        httpx_mock.add_response(
            url=re.compile(r".*/me/mailFolders/Inbox/messages/delta\?.*"),
            json={
                "value": [
                    self._msg("m1", "First", "2026-03-12T15:01:00Z"),
                    self._msg("m2", "Second", "2026-03-12T15:02:00Z"),
                ],
                "@odata.nextLink": pending,
            },
        )
        storage = LocalBackend(str(tmp_path / "vault"))
        # graph.max_pages is what caps the page walk now that $top carries the
        # item budget, so one page is what makes this round a capped one.
        client = GraphClient(graph_config.model_copy(update={"max_pages": 1}), lambda: "test-token")

        state, count = email.run(client, storage, {}, self._small_config(), ctx)
        assert count == 2
        assert state["delta_link_me_Inbox"] == pending

        httpx_mock.add_response(
            url=pending,
            json={
                "value": [self._msg("m3", "Third", "2026-03-12T15:03:00Z")],
                "@odata.deltaLink": "https://graph.microsoft.com/delta?token=final",
            },
        )
        state, count = email.run(client, storage, state, self._small_config(), ctx)

        assert count == 1
        assert state["delta_link_me_Inbox"] == "https://graph.microsoft.com/delta?token=final"
        assert len(storage.list_files(ctx.paths.inbox_root("email"))) == 3
        client.close()


class _GraphDeltaFolder:
    """Graph's measured delta contract, not a mock that echoes the request.

    `$top` on a delta query is a cap on the ENTIRE enumeration: the endpoint
    hands back at most that many items across all pages of the round, paged at
    its own size whatever `$top` said, and then closes with a deltaLink — so
    everything past the cap is never fetched and never resumed. A mock that
    returns exactly what the caller asked for is the mock that let a constant
    `$top=50` ship as a "page size" and cap every initial sync at 50 messages.

    `$filter` is likewise honoured the way Graph honours it on a message delta
    query: not at all, and without complaint. It is recorded, never applied.
    """

    def __init__(self, available: int, server_page_size: int) -> None:
        self.available = available
        self.server_page_size = server_page_size
        self.requested_top: str | None = None
        self.requested_filter: str | None = None
        self.rounds = 0
        self.served = 0

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if self.requested_top is None:
            self.requested_top = request.url.params.get("$top")
        if self.rounds == 0:
            self.requested_filter = request.url.params.get("$filter")
        self.rounds += 1

        cap = self.available if self.requested_top is None else min(self.available, int(self.requested_top))
        count = min(self.server_page_size, cap - self.served)
        value = [
            {
                "id": f"m{i}",
                "subject": f"Message {i}",
                "body": {"contentType": "text", "content": "body"},
                "from": {"emailAddress": {"name": "Test", "address": "test@example.com"}},
                "toRecipients": [],
                "receivedDateTime": f"2026-03-12T{i // 60:02d}:{i % 60:02d}:00Z",
                "importance": "normal",
                "hasAttachments": False,
                "webLink": "",
                "parentFolderId": "inbox",
            }
            for i in range(self.served, self.served + count)
        ]
        self.served += count

        body: dict = {"value": value}
        if self.served >= cap:
            body["@odata.deltaLink"] = "https://graph.example/delta?token=done"
        else:
            body["@odata.nextLink"] = f"https://graph.example/messages/delta?$skiptoken={self.served}"
        return httpx.Response(200, json=body)


class TestDeltaTopCarriesTheItemBudget:
    """Regression: `$top` is the item budget, so it must be the configured budget."""

    @staticmethod
    def _config(max_items: int) -> EmailExtractorConfig:
        return EmailExtractorConfig(
            enabled=True,
            poll_interval_minutes=3,
            mailboxes=[MailboxConfig(address="me", folders=["Inbox"], output_subdir="")],
            max_items_per_sync=max_items,
            download_attachments=False,
            max_attachment_size_mb=25,
            attachment_convert_extensions=[],
        )

    @respx.mock
    def test_top_sent_equals_the_configured_budget(self, tmp_path, graph_config, ctx):
        """The one line that would have caught this: no constant may set $top."""
        folder = _GraphDeltaFolder(available=200, server_page_size=10)
        respx.get(url__regex=r".*/messages/delta.*").mock(side_effect=folder)
        config = self._config(40)
        client = GraphClient(graph_config, lambda: "test-token")

        email.run(client, LocalBackend(str(tmp_path / "vault")), {}, config, ctx)

        assert folder.requested_top == str(config.max_items_per_sync)
        client.close()

    @respx.mock
    def test_initial_sync_fetches_the_whole_budget_not_one_page(self, tmp_path, graph_config, ctx):
        """A 40-message budget against a 200-message folder yields 40, not a page of 10."""
        folder = _GraphDeltaFolder(available=200, server_page_size=10)
        respx.get(url__regex=r".*/messages/delta.*").mock(side_effect=folder)
        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        _, count = email.run(client, storage, {}, self._config(40), ctx)

        assert count == 40
        assert len(storage.list_files(ctx.paths.inbox_root("email"))) == 40
        client.close()

    @respx.mock
    def test_budget_above_folder_size_takes_the_whole_folder(self, tmp_path, graph_config, ctx):
        """The budget is a ceiling, not a demand — a small folder still completes."""
        folder = _GraphDeltaFolder(available=25, server_page_size=10)
        respx.get(url__regex=r".*/messages/delta.*").mock(side_effect=folder)
        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        state, count = email.run(client, storage, {}, self._config(40), ctx)

        assert count == 25
        assert state["delta_link_me_Inbox"] == "https://graph.example/delta?token=done"
        client.close()


class TestInitialDeltaSendsNoFilter:
    """Regression: no time window on an email delta round, and none pretended.

    A message delta query does not support `$filter`; Graph ignores one instead
    of rejecting it, so a `receivedDateTime ge <cutoff>` derived from a
    `lookback_days` setting returned 200 OK and the whole folder anyway —
    measured at 1061 messages past a 90-day cutoff. Sending it back would
    silently restore that lie, so the assertion is that nothing is sent.
    """

    @respx.mock
    def test_initial_round_sends_no_filter(self, tmp_path, graph_config, ctx, email_config):
        folder = _GraphDeltaFolder(available=5, server_page_size=10)
        respx.get(url__regex=r".*/messages/delta.*").mock(side_effect=folder)
        client = GraphClient(graph_config, lambda: "test-token")

        email.run(client, LocalBackend(str(tmp_path / "vault")), {}, email_config, ctx)

        assert folder.requested_filter is None
        client.close()

    @respx.mock
    def test_initial_sync_takes_messages_of_any_age(self, tmp_path, graph_config, ctx, email_config):
        """The documented consequence: an initial sync enumerates the whole folder."""
        folder = _AgedDeltaFolder(received="2019-01-04T09:00:00Z")
        respx.get(url__regex=r".*/messages/delta.*").mock(side_effect=folder)
        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        _, count = email.run(client, storage, {}, email_config, ctx)

        assert count == 3
        assert len(storage.list_files(ctx.paths.inbox_root("email"))) == 3
        client.close()


class _AgedDeltaFolder:
    """A folder whose every message predates any window anyone would configure."""

    def __init__(self, received: str) -> None:
        self.received = received

    def __call__(self, request: httpx.Request) -> httpx.Response:
        value = [
            {
                "id": f"old{i}",
                "subject": f"Old message {i}",
                "body": {"contentType": "text", "content": "body"},
                "from": {"emailAddress": {"name": "Test", "address": "test@example.com"}},
                "toRecipients": [],
                "receivedDateTime": self.received,
                "importance": "normal",
                "hasAttachments": False,
                "webLink": "",
                "parentFolderId": "inbox",
            }
            for i in range(3)
        ]
        return httpx.Response(200, json={"value": value, "@odata.deltaLink": "https://graph.example/delta?token=done"})


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


class TestEmailDedup:
    """Emails with identical (received_minute, slug) are written only once per run."""

    def _make_msg(self, msg_id: str, subject: str, received: str) -> dict:
        return {
            "id": msg_id,
            "subject": subject,
            "body": {"contentType": "text", "content": "body"},
            "from": {"emailAddress": {"name": "Test", "address": "t@example.com"}},
            "toRecipients": [],
            "receivedDateTime": received,
            "importance": "normal",
            "hasAttachments": False,
            "webLink": "",
            "parentFolderId": "inbox",
        }

    def test_duplicate_within_run_is_skipped(self, httpx_mock: HTTPXMock, tmp_path, graph_config, email_config, ctx):
        """Two messages with the same subject and same received-minute are deduplicated."""
        httpx_mock.add_response(
            url=re.compile(r".*/me/mailFolders/Inbox/messages/delta.*"),
            json={
                "value": [
                    self._make_msg("id-a1b2c3", "Meeting notes", "2026-03-12T10:30:00Z"),
                    self._make_msg("id-d4e5f6", "Meeting notes", "2026-03-12T10:30:45Z"),  # same minute
                ],
                "@odata.deltaLink": "https://delta?token=dedup",
            },
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        _, count = email.run(client, storage, {}, email_config, ctx)

        # Only the first one should be written
        assert count == 1
        files = storage.list_files(ctx.paths.inbox_root("email"))
        assert len(files) == 1
        client.close()

    def test_different_minute_not_deduplicated(self, httpx_mock: HTTPXMock, tmp_path, graph_config, email_config, ctx):
        """Same subject but different received-minute = two distinct emails."""
        httpx_mock.add_response(
            url=re.compile(r".*/me/mailFolders/Inbox/messages/delta.*"),
            json={
                "value": [
                    self._make_msg("id-aaa111", "Status update", "2026-03-12T10:00:00Z"),
                    self._make_msg("id-bbb222", "Status update", "2026-03-12T10:01:00Z"),
                ],
                "@odata.deltaLink": "https://delta?token=nodeduplicate",
            },
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        _, count = email.run(client, storage, {}, email_config, ctx)
        assert count == 2
        client.close()

    def test_different_subject_not_deduplicated(self, httpx_mock: HTTPXMock, tmp_path, graph_config, email_config, ctx):
        """Different subjects at the same received-minute = two distinct emails."""
        httpx_mock.add_response(
            url=re.compile(r".*/me/mailFolders/Inbox/messages/delta.*"),
            json={
                "value": [
                    self._make_msg("id-ccc333", "Email A", "2026-03-12T10:00:00Z"),
                    self._make_msg("id-ddd444", "Email B", "2026-03-12T10:00:30Z"),
                ],
                "@odata.deltaLink": "https://delta?token=diffsubject",
            },
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        _, count = email.run(client, storage, {}, email_config, ctx)
        assert count == 2
        client.close()


# ---------------------------------------------------------------------------
# Attachment download
# ---------------------------------------------------------------------------


def _attachment_config() -> EmailExtractorConfig:
    return EmailExtractorConfig(
        enabled=True,
        poll_interval_minutes=3,
        mailboxes=[
            MailboxConfig(address="me", folders=["Inbox"], output_subdir=""),
        ],
        max_items_per_sync=100,
        download_attachments=True,
        max_attachment_size_mb=25,
        attachment_convert_extensions=[],
    )


class TestEmailAttachments:
    def _make_email_msg(self, has_attachments: bool = True) -> dict:
        return {
            "id": "msg-with-attachment",
            "subject": "See attached",
            "body": {"contentType": "text", "content": "Please see attachment."},
            "from": {"emailAddress": {"name": "Sender", "address": "sender@example.com"}},
            "toRecipients": [],
            "receivedDateTime": "2026-03-12T10:00:00Z",
            "importance": "normal",
            "hasAttachments": has_attachments,
            "webLink": "",
            "parentFolderId": "inbox",
        }

    def test_attachment_binary_written(self, httpx_mock: HTTPXMock, tmp_path, graph_config, ctx):
        """Attachment bytes are written to attachments/ subdir."""
        config = _attachment_config()
        httpx_mock.add_response(
            url=re.compile(r".*/me/mailFolders/Inbox/messages/delta.*"),
            json={
                "value": [self._make_email_msg()],
                "@odata.deltaLink": "https://delta?token=att",
            },
        )
        httpx_mock.add_response(
            url=re.compile(r".*/me/messages/msg-with-attachment/attachments.*"),
            json={
                "value": [
                    {
                        "id": "att-1",
                        "name": "report.pdf",
                        "contentType": "application/pdf",
                        "size": 1024,
                        "isInline": False,
                        "@microsoft.graph.downloadUrl": "https://attachments.office.com/report.pdf",
                    }
                ]
            },
        )
        httpx_mock.add_response(
            url="https://attachments.office.com/report.pdf",
            content=b"%PDF-1.4 fake content",
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        _, count = email.run(client, storage, {}, config, ctx)
        assert count == 1

        att_files = storage.list_files(ctx.paths.inbox_root("email"))
        att_paths = [f for f in att_files if ctx.paths.attachment("", "report.pdf") in f]
        assert len(att_paths) == 1
        client.close()

    def test_zone_identifier_attachment_skipped(self, httpx_mock: HTTPXMock, tmp_path, graph_config, ctx):
        """Attachments with ':' in name (Zone.Identifier artifacts) are skipped."""
        config = _attachment_config()
        httpx_mock.add_response(
            url=re.compile(r".*/me/mailFolders/Inbox/messages/delta.*"),
            json={
                "value": [self._make_email_msg()],
                "@odata.deltaLink": "https://delta?token=zone",
            },
        )
        httpx_mock.add_response(
            url=re.compile(r".*/me/messages/msg-with-attachment/attachments.*"),
            json={
                "value": [
                    {
                        "id": "att-zone",
                        "name": "Image.png:Zone.Identifier",
                        "contentType": "application/octet-stream",
                        "size": 100,
                        "isInline": False,
                        "@microsoft.graph.downloadUrl": "https://cdn.example.com/zone",
                    }
                ]
            },
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        _, count = email.run(client, storage, {}, config, ctx)
        assert count == 1

        # No attachment files written
        all_files = storage.list_files(ctx.paths.inbox_root("email"))
        att_paths = [f for f in all_files if ctx.paths.attachment("") + "/" in f]
        assert len(att_paths) == 0
        client.close()

    def test_inline_attachment_skipped(self, httpx_mock: HTTPXMock, tmp_path, graph_config, ctx):
        """Inline attachments (embedded images) are skipped."""
        config = _attachment_config()
        httpx_mock.add_response(
            url=re.compile(r".*/me/mailFolders/Inbox/messages/delta.*"),
            json={
                "value": [self._make_email_msg()],
                "@odata.deltaLink": "https://delta?token=inline",
            },
        )
        httpx_mock.add_response(
            url=re.compile(r".*/me/messages/msg-with-attachment/attachments.*"),
            json={
                "value": [
                    {
                        "id": "att-inline",
                        "name": "logo.png",
                        "contentType": "image/png",
                        "size": 2048,
                        "isInline": True,
                        "@microsoft.graph.downloadUrl": "https://cdn.example.com/logo.png",
                    }
                ]
            },
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        _, count = email.run(client, storage, {}, config, ctx)
        assert count == 1

        all_files = storage.list_files(ctx.paths.inbox_root("email"))
        att_paths = [f for f in all_files if ctx.paths.attachment("") + "/" in f]
        assert len(att_paths) == 0
        client.close()

    def test_oversized_attachment_skipped(self, httpx_mock: HTTPXMock, tmp_path, graph_config, ctx):
        """Attachments exceeding max_attachment_size_mb are skipped."""
        config = _attachment_config()
        httpx_mock.add_response(
            url=re.compile(r".*/me/mailFolders/Inbox/messages/delta.*"),
            json={
                "value": [self._make_email_msg()],
                "@odata.deltaLink": "https://delta?token=big",
            },
        )
        httpx_mock.add_response(
            url=re.compile(r".*/me/messages/msg-with-attachment/attachments.*"),
            json={
                "value": [
                    {
                        "id": "att-big",
                        "name": "huge.zip",
                        "contentType": "application/zip",
                        "size": 30 * 1024 * 1024,  # 30 MB — over the 25 MB limit
                        "isInline": False,
                        "@microsoft.graph.downloadUrl": "https://cdn.example.com/huge.zip",
                    }
                ]
            },
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        _, count = email.run(client, storage, {}, config, ctx)
        assert count == 1

        all_files = storage.list_files(ctx.paths.inbox_root("email"))
        att_paths = [f for f in all_files if ctx.paths.attachment("") + "/" in f]
        assert len(att_paths) == 0
        client.close()

    def test_download_attachments_false_skips_fetch(
        self, httpx_mock: HTTPXMock, tmp_path, graph_config, email_config, ctx
    ):
        """When download_attachments=False, attachments endpoint is never called."""
        # email_config fixture has download_attachments=False
        httpx_mock.add_response(
            url=re.compile(r".*/me/mailFolders/Inbox/messages/delta.*"),
            json={
                "value": [self._make_email_msg(has_attachments=True)],
                "@odata.deltaLink": "https://delta?token=nodl",
            },
        )
        # No mock for attachments endpoint — if called, httpx_mock would raise

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        _, count = email.run(client, storage, {}, email_config, ctx)
        assert count == 1
        client.close()

    def test_attachment_without_download_url_skipped(self, httpx_mock: HTTPXMock, tmp_path, graph_config, ctx):
        """Attachment with no downloadUrl and no contentBytes is skipped (no crash)."""
        config = _attachment_config()
        httpx_mock.add_response(
            url=re.compile(r".*/me/mailFolders/Inbox/messages/delta.*"),
            json={
                "value": [self._make_email_msg()],
                "@odata.deltaLink": "https://delta?token=nourl",
            },
        )
        httpx_mock.add_response(
            url=re.compile(r".*/me/messages/msg-with-attachment/attachments.*"),
            json={
                "value": [
                    {
                        "id": "att-nourl",
                        "name": "doc.docx",
                        "contentType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                        "size": 512,
                        "isInline": False,
                        # No @microsoft.graph.downloadUrl and no contentBytes
                    }
                ]
            },
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        _, count = email.run(client, storage, {}, config, ctx)
        assert count == 1

        all_files = storage.list_files(ctx.paths.inbox_root("email"))
        att_paths = [f for f in all_files if ctx.paths.attachment("") + "/" in f]
        assert len(att_paths) == 0
        client.close()

    def test_attachment_content_bytes_fallback(self, httpx_mock: HTTPXMock, tmp_path, graph_config, ctx):
        """Attachment with contentBytes (base64) is decoded and written when no downloadUrl."""
        import base64

        config = _attachment_config()
        fake_content = b"fake xlsx content"
        encoded = base64.b64encode(fake_content).decode()
        httpx_mock.add_response(
            url=re.compile(r".*/me/mailFolders/Inbox/messages/delta.*"),
            json={
                "value": [self._make_email_msg()],
                "@odata.deltaLink": "https://delta?token=cb",
            },
        )
        httpx_mock.add_response(
            url=re.compile(r".*/me/messages/msg-with-attachment/attachments.*"),
            json={
                "value": [
                    {
                        "id": "att-cb",
                        "name": "data.xlsx",
                        "contentType": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        "size": len(fake_content),
                        "isInline": False,
                        "contentBytes": encoded,
                        # No @microsoft.graph.downloadUrl
                    }
                ]
            },
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        _, count = email.run(client, storage, {}, config, ctx)
        assert count == 1

        all_files = storage.list_files(ctx.paths.inbox_root("email"))
        att_paths = [f for f in all_files if ctx.paths.attachment("", "data.xlsx") in f]
        assert len(att_paths) == 1
        client.close()

    def test_attachment_download_failure_logs_warning(self, httpx_mock: HTTPXMock, tmp_path, graph_config, ctx):
        """When client.get_bytes raises a caught error, the failure is logged and other attachments continue."""
        config = _attachment_config()
        httpx_mock.add_response(
            url=re.compile(r".*/me/mailFolders/Inbox/messages/delta.*"),
            json={
                "value": [self._make_email_msg()],
                "@odata.deltaLink": "https://delta?token=fail",
            },
        )
        httpx_mock.add_response(
            url=re.compile(r".*/me/messages/msg-with-attachment/attachments.*"),
            json={
                "value": [
                    {
                        "id": "att-fail",
                        "name": "broken.pdf",
                        "contentType": "application/pdf",
                        "size": 256,
                        "isInline": False,
                        "@microsoft.graph.downloadUrl": "https://attachments.office.com/broken.pdf",
                    }
                ]
            },
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        warnings: list[dict] = []

        def capture_warning(event, **kwargs):
            warnings.append({"event": event, **kwargs})

        with (
            patch.object(client, "get_bytes", side_effect=OSError("network error")),
            patch.object(_attachment_helpers.log, "warning", side_effect=capture_warning),
        ):
            _, count = email.run(client, storage, {}, config, ctx)

        assert count == 1
        download_failures = [w for w in warnings if w["event"] == "email.attachment_download_failed"]
        assert len(download_failures) == 1
        assert download_failures[0]["name"] == "broken.pdf"
        assert "network error" in download_failures[0]["error"]

        # No attachment file should have been written
        all_files = storage.list_files(ctx.paths.inbox_root("email"))
        att_paths = [f for f in all_files if ctx.paths.attachment("") + "/" in f]
        assert len(att_paths) == 0
        client.close()

    def test_attachment_triggers_convert_when_extension_matches(
        self, httpx_mock: HTTPXMock, tmp_path, graph_config, ctx
    ):
        """Attachment with matching extension is converted via _convert_and_store."""
        config = EmailExtractorConfig(
            enabled=True,
            poll_interval_minutes=3,
            mailboxes=[
                MailboxConfig(address="me", folders=["Inbox"], output_subdir=""),
            ],
            max_items_per_sync=100,
            download_attachments=True,
            max_attachment_size_mb=25,
            attachment_convert_extensions=[".pdf"],
        )
        httpx_mock.add_response(
            url=re.compile(r".*/me/mailFolders/Inbox/messages/delta.*"),
            json={
                "value": [self._make_email_msg()],
                "@odata.deltaLink": "https://delta?token=conv",
            },
        )
        httpx_mock.add_response(
            url=re.compile(r".*/me/messages/msg-with-attachment/attachments.*"),
            json={
                "value": [
                    {
                        "id": "att-conv",
                        "name": "report.pdf",
                        "contentType": "application/pdf",
                        "size": 64,
                        "isInline": False,
                        "@microsoft.graph.downloadUrl": "https://attachments.office.com/report.pdf",
                    }
                ]
            },
        )
        httpx_mock.add_response(
            url="https://attachments.office.com/report.pdf",
            content=b"%PDF-1.4 fake pdf",
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        with patch.object(_attachment_helpers, "convert_document", return_value="# Converted\n\nbody") as mock_conv:
            _, count = email.run(client, storage, {}, config, ctx)

        assert count == 1
        assert mock_conv.call_count == 1

        all_files = storage.list_files(ctx.paths.inbox_root("email"))
        converted = [f for f in all_files if ctx.paths.converted_attachment("", "report.pdf.md") in f]
        assert len(converted) == 1
        assert "# Converted" in storage.read_file(converted[0])
        client.close()

    @pytest.mark.parametrize(
        "malicious_name",
        [
            "../../etc/passwd",
            "../sibling/file.txt",
            "subdir/file.pdf",
            "a/b/c/d.txt",
        ],
    )
    def test_path_traversal_attachment_name_stripped_to_basename(
        self, httpx_mock: HTTPXMock, tmp_path, graph_config, malicious_name, ctx
    ):
        """Attachment names with path components are stripped to basename only."""
        config = _attachment_config()
        expected_basename = Path(malicious_name).name
        httpx_mock.add_response(
            url=re.compile(r".*/me/mailFolders/Inbox/messages/delta.*"),
            json={
                "value": [self._make_email_msg()],
                "@odata.deltaLink": "https://delta?token=traversal",
            },
        )
        httpx_mock.add_response(
            url=re.compile(r".*/me/messages/msg-with-attachment/attachments.*"),
            json={
                "value": [
                    {
                        "id": "att-traversal",
                        "name": malicious_name,
                        "contentType": "application/octet-stream",
                        "size": 64,
                        "isInline": False,
                        "@microsoft.graph.downloadUrl": "https://attachments.office.com/traversal",
                    }
                ]
            },
        )
        httpx_mock.add_response(
            url="https://attachments.office.com/traversal",
            content=b"payload",
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        _, count = email.run(client, storage, {}, config, ctx)
        assert count == 1

        all_files = storage.list_files(ctx.paths.inbox_root("email"))
        att_paths = [f for f in all_files if ctx.paths.attachment("") + "/" in f]
        assert len(att_paths) == 1
        assert att_paths[0].endswith(ctx.paths.attachment("", expected_basename))
        client.close()


# ---------------------------------------------------------------------------
# Multi-mailbox routing
# ---------------------------------------------------------------------------


class TestSharedMailbox:
    """Verify the shared mailbox path uses /users/{address}/... and namespaces storage."""

    def _config(self, mailboxes: list[MailboxConfig]) -> EmailExtractorConfig:
        return EmailExtractorConfig(
            enabled=True,
            poll_interval_minutes=3,
            mailboxes=mailboxes,
            max_items_per_sync=100,
            download_attachments=False,
            max_attachment_size_mb=25,
            attachment_convert_extensions=[],
        )

    def _msg(self, msg_id: str, subject: str, received: str) -> dict:
        return {
            "id": msg_id,
            "subject": subject,
            "body": {"contentType": "text", "content": "body"},
            "from": {"emailAddress": {"name": "S", "address": "s@example.com"}},
            "toRecipients": [],
            "receivedDateTime": received,
            "importance": "normal",
            "hasAttachments": False,
            "webLink": "",
            "parentFolderId": "inbox",
        }

    def test_shared_mailbox_uses_users_endpoint_and_subdir(self, httpx_mock: HTTPXMock, tmp_path, graph_config, ctx):
        config = self._config([MailboxConfig(address="ai@example.com", folders=["Inbox"], output_subdir="ai-example")])
        httpx_mock.add_response(
            url=re.compile(r".*/users/ai@example\.com/mailFolders/Inbox/messages/delta.*"),
            json={
                "value": [self._msg("msg-shared-1", "Hello shared", "2026-05-08T10:00:00Z")],
                "@odata.deltaLink": "https://delta?token=shared",
            },
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        state, count = email.run(client, storage, {}, config, ctx)

        assert count == 1
        assert "delta_link_ai@example.com_Inbox" in state

        # Storage path must be namespaced under the output_subdir
        files = storage.list_files(ctx.paths.inbox_root("email"))
        assert any(ctx.paths.inbox_item("email", "ai-example", "2026", "2026-05-08") + "/" in f for f in files)
        # And NOT placed at the top-level emails/{year}/...
        assert not any(f.startswith(ctx.paths.inbox_item("email", "2026") + "/") for f in files)

        # Frontmatter must record the mailbox address
        content = storage.read_file(files[0])
        from m365_brain.m365.markdown_writer import loads_markdown

        fm, _ = loads_markdown(content)
        assert fm["mailbox"] == "ai@example.com"
        client.close()

    def test_personal_and_shared_isolated_in_one_run(self, httpx_mock: HTTPXMock, tmp_path, graph_config, ctx):
        config = self._config(
            [
                MailboxConfig(address="me", folders=["Inbox"], output_subdir=""),
                MailboxConfig(address="ai@example.com", folders=["Inbox"], output_subdir="ai-example"),
            ]
        )
        httpx_mock.add_response(
            url=re.compile(r".*/me/mailFolders/Inbox/messages/delta.*"),
            json={
                "value": [self._msg("msg-personal", "Personal", "2026-05-08T09:00:00Z")],
                "@odata.deltaLink": "https://delta?token=me",
            },
        )
        httpx_mock.add_response(
            url=re.compile(r".*/users/ai@example\.com/mailFolders/Inbox/messages/delta.*"),
            json={
                "value": [self._msg("msg-shared", "Shared", "2026-05-08T10:00:00Z")],
                "@odata.deltaLink": "https://delta?token=ai",
            },
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        state, count = email.run(client, storage, {}, config, ctx)

        assert count == 2
        assert "delta_link_me_Inbox" in state
        assert "delta_link_ai@example.com_Inbox" in state

        files = storage.list_files(ctx.paths.inbox_root("email"))
        personal = [f for f in files if f.startswith(ctx.paths.inbox_item("email", "2026") + "/")]
        shared = [f for f in files if f.startswith(ctx.paths.inbox_item("email", "ai-example") + "/")]
        assert len(personal) == 1
        assert len(shared) == 1
        client.close()

    def test_auto_discover_filters_system_folders(self, httpx_mock: HTTPXMock, tmp_path, graph_config, ctx):
        """When folders=None, discovery uses GET /mailFolders and skips Drafts/Junk/etc."""
        config = self._config([MailboxConfig(address="ai@example.com", folders=None, output_subdir="ai-example")])

        # Discovery response — mix of keep + skip folders.
        # URL params are encoded ($select=id%2CdisplayName...), so match loosely on
        # the listing endpoint, distinguished from /mailFolders/{id}/messages by the
        # trailing `?` indicating a query-string list call rather than a sub-resource.
        # Graph API v1.0 returns `isHidden`; `wellKnownName` is beta-only and not selected.
        httpx_mock.add_response(
            url=re.compile(r".*/users/ai@example\.com/mailFolders\?.*"),
            json={
                "value": [
                    {"id": "id-inbox", "displayName": "Inbox", "isHidden": False},
                    {"id": "id-drafts", "displayName": "Drafts", "isHidden": False},
                    {"id": "id-junk", "displayName": "Junk Email", "isHidden": False},
                    {"id": "id-projects", "displayName": "Projects", "isHidden": False},
                    {"id": "id-deleted", "displayName": "Deleted Items", "isHidden": False},
                    {"id": "id-hidden", "displayName": "Internal", "isHidden": True},
                ]
            },
        )

        # Inbox delta (uses well-known "Inbox" as the folder ID)
        httpx_mock.add_response(
            url=re.compile(r".*/users/ai@example\.com/mailFolders/Inbox/messages/delta.*"),
            json={
                "value": [self._msg("m-1", "in inbox", "2026-05-08T10:00:00Z")],
                "@odata.deltaLink": "https://delta?token=inbox",
            },
        )
        # Projects delta — uses the resolved folder id from discovery cache
        httpx_mock.add_response(
            url=re.compile(r".*/users/ai@example\.com/mailFolders/id-projects/messages/delta.*"),
            json={
                "value": [self._msg("m-2", "in projects", "2026-05-08T10:00:00Z")],
                "@odata.deltaLink": "https://delta?token=projects",
            },
        )

        storage = LocalBackend(str(tmp_path / "vault"))
        client = GraphClient(graph_config, lambda: "test-token")

        state, count = email.run(client, storage, {}, config, ctx)

        # Only Inbox + Projects synced; Drafts/Junk/Deleted skipped by displayName,
        # Internal skipped by isHidden=true.
        assert count == 2
        assert "delta_link_ai@example.com_Inbox" in state
        assert "delta_link_ai@example.com_Projects" in state
        assert "delta_link_ai@example.com_Drafts" not in state
        assert "delta_link_ai@example.com_Junk Email" not in state
        assert "delta_link_ai@example.com_Deleted Items" not in state
        assert "delta_link_ai@example.com_Internal" not in state
        client.close()


# ---------------------------------------------------------------------------
# Custom folder resolution (_resolve_folder_id)
# ---------------------------------------------------------------------------


class TestResolveFolderId:
    """Tests for resolve_folder_id: well-known folders, Graph API lookup, and caching."""

    def test_well_known_folder_returns_predefined_id(self):
        """Well-known folders (Inbox, SentItems, etc.) return their predefined ID without calling the API."""
        client = MagicMock(spec=GraphClient)
        cache: dict[tuple[str, str], str] = {}
        assert _folder_helpers.resolve_folder_id(client, "/me", "me", "Inbox", cache) == "Inbox"
        assert _folder_helpers.resolve_folder_id(client, "/me", "me", "SentItems", cache) == "SentItems"
        client.get.assert_not_called()

    def test_custom_folder_resolved_via_graph_api(self):
        """Custom folder name is resolved to its ID via Graph API query."""
        client = MagicMock(spec=GraphClient)
        client.get.return_value = {"value": [{"id": "abc123", "displayName": "Archive-Custom"}]}
        cache: dict[tuple[str, str], str] = {}

        result = _folder_helpers.resolve_folder_id(client, "/me", "me", "Archive-Custom", cache)

        assert result == "abc123"
        client.get.assert_called_once_with(
            "/me/mailFolders",
            {"$filter": "displayName eq 'Archive-Custom'", "$select": "id,displayName", "$top": "1"},
        )

    def test_single_quotes_escaped_in_odata_filter(self):
        """Single quotes in folder names are doubled to prevent OData filter injection."""
        client = MagicMock(spec=GraphClient)
        client.get.return_value = {"value": [{"id": "obrien-id", "displayName": "O'Brien"}]}

        result = _folder_helpers.resolve_folder_id(client, "/me", "me", "O'Brien", {})

        assert result == "obrien-id"
        client.get.assert_called_once_with(
            "/me/mailFolders",
            {"$filter": "displayName eq 'O''Brien'", "$select": "id,displayName", "$top": "1"},
        )

    def test_custom_folder_cached_after_first_resolution(self):
        """Second call with the same custom folder name uses cache — Graph API not called again."""
        client = MagicMock(spec=GraphClient)
        client.get.return_value = {"value": [{"id": "folder-xyz", "displayName": "Projects"}]}
        cache: dict[tuple[str, str], str] = {}

        first = _folder_helpers.resolve_folder_id(client, "/me", "me", "Projects", cache)
        second = _folder_helpers.resolve_folder_id(client, "/me", "me", "Projects", cache)

        assert first == "folder-xyz"
        assert second == "folder-xyz"
        assert client.get.call_count == 1

    def test_custom_folder_not_found_raises_graph_api_error(self):
        """Empty response from Graph API raises GraphApiError with helpful message."""
        client = MagicMock(spec=GraphClient)
        client.get.return_value = {"value": []}
        cache: dict[tuple[str, str], str] = {}

        with pytest.raises(GraphApiError, match="Mail folder not found: 'NonExistent'"):
            _folder_helpers.resolve_folder_id(client, "/me", "me", "NonExistent", cache)

    def test_not_found_folder_not_cached(self):
        """Failed resolution does not pollute the cache."""
        client = MagicMock(spec=GraphClient)
        client.get.return_value = {"value": []}
        cache: dict[tuple[str, str], str] = {}

        with pytest.raises(GraphApiError):
            _folder_helpers.resolve_folder_id(client, "/me", "me", "Ghost", cache)

        assert ("me", "Ghost") not in cache

    def test_custom_folder_cache_keyed_by_mailbox(self):
        """The same folder name in different mailboxes resolves independently."""
        client = MagicMock(spec=GraphClient)
        client.get.side_effect = [
            {"value": [{"id": "id-personal", "displayName": "Projects"}]},
            {"value": [{"id": "id-shared", "displayName": "Projects"}]},
        ]
        cache: dict[tuple[str, str], str] = {}

        first = _folder_helpers.resolve_folder_id(client, "/me", "me", "Projects", cache)
        second = _folder_helpers.resolve_folder_id(client, "/users/ai@example.com", "ai@example.com", "Projects", cache)

        assert first == "id-personal"
        assert second == "id-shared"
        assert client.get.call_count == 2
        assert client.get.call_args_list[1].args[0] == "/users/ai@example.com/mailFolders"


class TestFolderCacheIsolation:
    """Concurrent extractions for different users must not share folder-id state."""

    def test_concurrent_users_get_independent_caches(self):
        """Two threads resolving the same folder name get independent cache dicts."""
        import threading

        results: dict[str, str] = {}
        errors: list[Exception] = []

        def resolve_for_user(address: str, expected_id: str) -> None:
            try:
                client = MagicMock(spec=GraphClient)
                client.get.return_value = {"value": [{"id": expected_id, "displayName": "Projects"}]}
                cache: dict[tuple[str, str], str] = {}
                result = _folder_helpers.resolve_folder_id(client, f"/users/{address}", address, "Projects", cache)
                results[address] = result
                assert (address, "Projects") in cache
            except Exception as exc:
                errors.append(exc)

        t1 = threading.Thread(target=resolve_for_user, args=("alice@example.com", "id-alice"))
        t2 = threading.Thread(target=resolve_for_user, args=("bob@example.com", "id-bob"))

        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert not errors, f"Thread errors: {errors}"
        assert results["alice@example.com"] == "id-alice"
        assert results["bob@example.com"] == "id-bob"


class TestNarrowedExceptionHandling:
    """Verify that narrowed except clauses catch expected errors but propagate programming errors."""

    def test_download_graph_api_error_caught(self, tmp_path, ctx):
        """GraphApiError during attachment download is caught (log-and-continue)."""
        storage = LocalBackend(str(tmp_path / "vault"))
        client = MagicMock(spec=GraphClient)
        client.get_paginated.return_value = iter(
            [{"name": "file.pdf", "size": 100, "isInline": False, "@microsoft.graph.downloadUrl": "https://cdn/f"}]
        )
        client.get_bytes.side_effect = GraphApiError("404 Not Found", 404)

        config = _attachment_config()
        _attachment_helpers.download_attachments(
            client, storage, "/me", "msg-1", ctx.paths.inbox_item("email", "2026", "dir"), config, ctx
        )

    def test_fetch_attachments_graph_api_error_caught(self, tmp_path, ctx):
        """GraphApiError during attachment list fetch is caught (log-and-continue)."""
        storage = LocalBackend(str(tmp_path / "vault"))
        client = MagicMock(spec=GraphClient)
        client.get_paginated.side_effect = GraphApiError("500 Server Error", 500)

        config = _attachment_config()
        _attachment_helpers.download_attachments(
            client, storage, "/me", "msg-1", ctx.paths.inbox_item("email", "2026", "dir"), config, ctx
        )

    def test_download_type_error_propagates(self, tmp_path, ctx):
        """TypeError during attachment download propagates (programming error)."""
        storage = LocalBackend(str(tmp_path / "vault"))
        client = MagicMock(spec=GraphClient)
        client.get_paginated.return_value = iter(
            [{"name": "file.pdf", "size": 100, "isInline": False, "@microsoft.graph.downloadUrl": "https://cdn/f"}]
        )
        client.get_bytes.side_effect = TypeError("unexpected None")

        config = _attachment_config()
        with pytest.raises(TypeError):
            _attachment_helpers.download_attachments(
                client, storage, "/me", "msg-1", ctx.paths.inbox_item("email", "2026", "dir"), config, ctx
            )

    def test_fetch_attachments_type_error_propagates(self, tmp_path, ctx):
        """TypeError during attachment list fetch propagates (programming error)."""
        storage = LocalBackend(str(tmp_path / "vault"))
        client = MagicMock(spec=GraphClient)
        client.get_paginated.side_effect = TypeError("bad argument")

        config = _attachment_config()
        with pytest.raises(TypeError):
            _attachment_helpers.download_attachments(
                client, storage, "/me", "msg-1", ctx.paths.inbox_item("email", "2026", "dir"), config, ctx
            )

    def test_convert_os_error_caught(self, tmp_path):
        """OSError during attachment conversion is caught (log-and-continue)."""
        storage = LocalBackend(str(tmp_path / "vault"))
        with patch("m365_brain.m365.extractors._attachment_helpers.convert_document", side_effect=OSError("disk full")):
            _attachment_helpers.convert_and_store(
                storage, b"data", "file.pdf", "emails/dir/attachments_converted/file.pdf.md", {}
            )

    def test_convert_attribute_error_propagates(self, tmp_path):
        """AttributeError during conversion propagates (programming error)."""
        storage = LocalBackend(str(tmp_path / "vault"))
        with (
            patch(
                "m365_brain.m365.extractors._attachment_helpers.convert_document", side_effect=AttributeError("oops")
            ),
            pytest.raises(AttributeError),
        ):
            _attachment_helpers.convert_and_store(
                storage, b"data", "file.pdf", "emails/dir/attachments_converted/file.pdf.md", {}
            )


# ---------------------------------------------------------------------------
# _convert_and_store unit tests
# ---------------------------------------------------------------------------


class TestConvertAndStore:
    """Unit tests for _convert_and_store covering happy path, failure, and tmp cleanup."""

    def test_conversion_error_returns_false_without_raising(self, tmp_path):
        """An obsidian-import failure (e.g. timeout) must not propagate past convert_and_store."""
        from m365_brain.m365.converters.document import DocumentConversionError

        storage = MagicMock()
        with patch.object(
            _attachment_helpers,
            "convert_document",
            side_effect=DocumentConversionError("markitdown extraction timed out after 120s: big.xlsx"),
        ):
            ok = _attachment_helpers.convert_and_store(
                storage=storage,
                data=b"binary-data",
                source_name="big.xlsx",
                target_path="x/attachments_converted/big.xlsx.md",
                converters_config={},
            )

        assert ok is False
        storage.write_file.assert_not_called()

    def test_happy_path_writes_markdown(self, tmp_path):
        """convert_document returns markdown; storage.write_file gets correct path/content."""
        storage = MagicMock()
        with patch.object(_attachment_helpers, "convert_document", return_value="# Hello\n\ncontent") as mock_conv:
            ok = _attachment_helpers.convert_and_store(
                storage=storage,
                data=b"binary-data",
                source_name="report.pdf",
                target_path="emails/2026/2026-03-12/sub-abc123/attachments_converted/report.pdf.md",
                converters_config={},
            )

        assert ok is True
        mock_conv.assert_called_once()
        called_path = mock_conv.call_args.args[0]
        assert isinstance(called_path, Path)
        assert called_path.suffix == ".pdf"

        storage.write_file.assert_called_once_with(
            "emails/2026/2026-03-12/sub-abc123/attachments_converted/report.pdf.md",
            "# Hello\n\ncontent",
        )

    def test_conversion_failure_logs_warning_no_raise(self, tmp_path):
        """When convert_document raises a caught error, the warning is logged and no exception escapes."""
        storage = MagicMock()
        warnings: list[dict] = []

        def capture(event, **kwargs):
            warnings.append({"event": event, **kwargs})

        with (
            patch.object(_attachment_helpers, "convert_document", side_effect=OSError("bad pdf")),
            patch.object(_attachment_helpers.log, "warning", side_effect=capture),
        ):
            _attachment_helpers.convert_and_store(
                storage=storage,
                data=b"junk",
                source_name="bad.pdf",
                target_path="emails/2026/2026-03-12/dir/attachments_converted/bad.pdf.md",
                converters_config={},
            )

        # storage.write_file must NOT have been called when conversion fails
        storage.write_file.assert_not_called()

        convert_failures = [w for w in warnings if w["event"] == "attachment.convert_failed"]
        assert len(convert_failures) == 1
        assert convert_failures[0]["name"] == "bad.pdf"
        assert "bad pdf" in convert_failures[0]["error"]

    def test_tmp_path_cleaned_up_on_failure(self, tmp_path):
        """tmp file is deleted by the finally block even when convert_document raises a caught error."""
        storage = MagicMock()
        captured_paths: list[Path] = []

        def capture_path_and_raise(path: Path, _config: dict) -> str:
            captured_paths.append(path)
            assert path.exists(), "tmp file should exist when convert_document is invoked"
            raise OSError("conversion blew up")

        with patch.object(_attachment_helpers, "convert_document", side_effect=capture_path_and_raise):
            _attachment_helpers.convert_and_store(
                storage=storage,
                data=b"bytes",
                source_name="doc.docx",
                target_path="emails/2026/2026-03-12/dir/attachments_converted/doc.docx.md",
                converters_config={},
            )

        assert len(captured_paths) == 1
        # finally block must have unlinked the tmp file
        assert not captured_paths[0].exists()

    def test_tmp_path_cleaned_up_on_success(self, tmp_path):
        """tmp file is deleted by the finally block on the happy path too."""
        storage = MagicMock()
        captured_paths: list[Path] = []

        def capture_path(path: Path, _config: dict) -> str:
            captured_paths.append(path)
            return "# md"

        with patch.object(_attachment_helpers, "convert_document", side_effect=capture_path):
            _attachment_helpers.convert_and_store(
                storage=storage,
                data=b"bytes",
                source_name="doc.docx",
                target_path="emails/2026/2026-03-12/dir/attachments_converted/doc.docx.md",
                converters_config={},
            )

        assert len(captured_paths) == 1
        assert not captured_paths[0].exists()


# ---------------------------------------------------------------------------
# Guard branch coverage: _attachment_helpers line 41
# ---------------------------------------------------------------------------


class TestAttachmentEmptyPathName:
    """Attachment with name that resolves to empty after Path().name is skipped."""

    def test_download_attachments_skips_empty_path_name(self, tmp_path, ctx):
        """Attachment with name='/' passes the ':' check but Path('/').name == '', so it's skipped."""
        storage = LocalBackend(str(tmp_path / "vault"))
        client = MagicMock(spec=GraphClient)
        client.get_paginated.return_value = iter(
            [
                {
                    "name": "/",
                    "size": 100,
                    "isInline": False,
                    "@microsoft.graph.downloadUrl": "https://cdn/slash",
                }
            ]
        )

        config = _attachment_config()
        _attachment_helpers.download_attachments(
            client, storage, "/me", "msg-1", ctx.paths.inbox_item("email", "2026", "dir"), config, ctx
        )

        client.get_bytes.assert_not_called()
        assert storage.list_files(ctx.paths.inbox_root("email")) == []


# ---------------------------------------------------------------------------
# Guard branch coverage: _folder_helpers line 113
# ---------------------------------------------------------------------------


def _folder_pages(*folders: dict) -> MagicMock:
    """A client whose paged `mailFolders` walk serves one page of `folders`."""
    client = MagicMock(spec=GraphClient)
    client.max_pages = 10
    client.get_pages.side_effect = lambda path, params, cap: (
        list(folders) if path.endswith("mailFolders") else [],
        False,
    )
    return client


class TestListAllFoldersGuardBranches:
    """list_all_folders skips folder entries with missing displayName or id."""

    def test_skips_missing_display_name(self):
        """Folder with displayName=None is excluded from the result."""
        client = _folder_pages(
            {"id": "id-valid", "displayName": "Projects", "isHidden": False},
            {"id": "id-no-name", "displayName": None, "isHidden": False},
        )

        result = _folder_helpers.list_all_folders(client, "/me", "me")

        assert result == [("Projects", "id-valid")]

    def test_skips_absent_display_name(self):
        """Folder with no displayName key at all is excluded from the result."""
        client = _folder_pages(
            {"id": "id-valid", "displayName": "Inbox", "isHidden": False},
            {"id": "id-missing-key", "isHidden": False},
        )

        result = _folder_helpers.list_all_folders(client, "/me", "me")

        assert result == [("Inbox", "id-valid")]

    def test_skips_missing_id(self):
        """Folder with id=None is excluded from the result."""
        client = _folder_pages(
            {"id": "id-good", "displayName": "Archive", "isHidden": False},
            {"id": None, "displayName": "Broken", "isHidden": False},
        )

        result = _folder_helpers.list_all_folders(client, "/me", "me")

        assert result == [("Archive", "id-good")]

    def test_skips_absent_id(self):
        """Folder with no id key at all is excluded from the result."""
        client = _folder_pages(
            {"id": "id-good", "displayName": "Sent", "isHidden": False},
            {"displayName": "NoId", "isHidden": False},
        )

        result = _folder_helpers.list_all_folders(client, "/me", "me")

        assert result == [("Sent", "id-good")]
