"""Tests for the channel fetch-and-convert pipeline."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from m365_brain.m365.client import GraphClient
from m365_brain.m365.extractors import _teams_channel_ingest as ingest
from m365_brain.m365.extractors._message_store import StoredMessage
from m365_brain.m365.extractors._teams_channel_ingest import chain_modified, convert_chains, fetch_chains
from m365_brain.m365.extractors._teams_context import TeamsContext

MESSAGES_URL = "/teams/t1/channels/c1/messages"


def _client(pages: dict[str, dict]) -> MagicMock:
    """A GraphClient whose ``get`` serves canned responses keyed by URL."""
    client = MagicMock(spec=GraphClient)
    client.get.side_effect = lambda url, params=None: pages[url]
    return client


def _msg(msg_id: str, modified: str) -> dict:
    return {
        "id": msg_id,
        "messageType": "message",
        "etag": f"etag-{msg_id}",
        "createdDateTime": modified,
        "lastModifiedDateTime": modified,
        "body": {"contentType": "text", "content": f"body of {msg_id}"},
        "from": {"user": {"displayName": "Ada"}},
    }


def _ctx(teams_channels_config, local_storage, vault_paths) -> TeamsContext:
    return TeamsContext(
        client=MagicMock(spec=GraphClient),
        storage=local_storage,
        settings=teams_channels_config,
        converters_config={},
        failed_attachments={},
        conv_dir=vault_paths.inbox_item("teams_channels", "team", "chan_abc"),
        paths=vault_paths,
    )


class TestChainModified:
    def test_takes_the_latest_timestamp_across_root_and_replies(self) -> None:
        root = _msg("r1", "2026-03-01T00:00:00Z")
        replies = [_msg("a1", "2026-03-05T00:00:00Z"), _msg("a2", "2026-03-03T00:00:00Z")]
        assert chain_modified(root, replies) == "2026-03-05T00:00:00Z"

    def test_falls_back_to_created_when_last_modified_is_absent(self) -> None:
        root = {"createdDateTime": "2026-03-02T00:00:00Z"}
        reply = {"createdDateTime": "2026-03-01T00:00:00Z", "lastModifiedDateTime": None}
        assert chain_modified(root, [reply]) == "2026-03-02T00:00:00Z"


class TestFetchChains:
    def test_stops_at_the_watermark_and_leaves_the_next_page_unfetched(self) -> None:
        client = _client(
            {
                MESSAGES_URL: {
                    "value": [_msg("new", "2026-03-05T00:00:00Z"), _msg("old", "2026-03-01T00:00:00Z")],
                    "@odata.nextLink": "https://graph.microsoft.test/page2",
                }
            }
        )
        chains, truncated = fetch_chains(client, "t1", "c1", "2026-03-02T00:00:00Z", max_messages=100)

        assert [root["id"] for root, _ in chains] == ["new"]
        assert truncated is False
        assert client.get.call_count == 1

    def test_reply_pagination_is_followed_to_the_end(self) -> None:
        root = _msg("r1", "2026-03-05T00:00:00Z")
        root["replies"] = [_msg("a1", "2026-03-05T00:00:00Z")]
        root["replies@odata.nextLink"] = "https://graph.microsoft.test/replies2"
        client = _client(
            {
                MESSAGES_URL: {"value": [root]},
                "https://graph.microsoft.test/replies2": {"value": [_msg("a2", "2026-03-05T00:00:00Z")]},
            }
        )

        chains, _ = fetch_chains(client, "t1", "c1", None, max_messages=100)

        assert [reply["id"] for reply in chains[0][1]] == ["a1", "a2"]

    def test_backfill_stops_at_the_message_cap_and_reports_truncation(self) -> None:
        first = _msg("r1", "2026-03-05T00:00:00Z")
        first["replies"] = [_msg("a1", "2026-03-05T00:00:00Z"), _msg("a2", "2026-03-05T00:00:00Z")]
        client = _client(
            {
                MESSAGES_URL: {
                    "value": [first, _msg("r2", "2026-03-04T00:00:00Z")],
                    "@odata.nextLink": "https://graph.microsoft.test/page2",
                }
            }
        )

        chains, truncated = fetch_chains(client, "t1", "c1", None, max_messages=3)

        assert [root["id"] for root, _ in chains] == ["r1"]
        assert truncated is True
        assert client.get.call_count == 1

    def test_pages_forward_sending_query_params_only_once(self) -> None:
        client = _client(
            {
                MESSAGES_URL: {
                    "value": [_msg("r1", "2026-03-05T00:00:00Z")],
                    "@odata.nextLink": "https://graph.microsoft.test/page2",
                },
                "https://graph.microsoft.test/page2": {"value": [_msg("r2", "2026-03-04T00:00:00Z")]},
            }
        )

        chains, truncated = fetch_chains(client, "t1", "c1", None, max_messages=100)

        assert [root["id"] for root, _ in chains] == ["r1", "r2"]
        assert truncated is False
        first_call, second_call = client.get.call_args_list
        assert first_call.kwargs["params"]["$expand"] == "replies"
        assert second_call.kwargs["params"] is None


class TestConvertChains:
    def test_root_and_reply_wiring(self, teams_channels_config, local_storage, vault_paths) -> None:
        root = _msg("r1", "2026-03-05T00:00:00Z")
        root["subject"] = "Release plan"
        reply = _msg("a1", "2026-03-05T00:00:00Z")

        converted = convert_chains(
            _ctx(teams_channels_config, local_storage, vault_paths), [(root, [reply])], {}, MESSAGES_URL
        )

        assert [m.id for m in converted] == ["r1", "a1"]
        assert converted[0].parent_id is None
        assert converted[0].subject == "Release plan"
        assert converted[0].content == "body of r1"
        assert converted[0].sender == "Ada"
        assert converted[1].parent_id == "r1"
        assert converted[1].subject is None

    def test_fresh_etags_and_non_message_roots_are_skipped(
        self, teams_channels_config, local_storage, vault_paths
    ) -> None:
        system_root = _msg("r1", "2026-03-05T00:00:00Z")
        system_root["messageType"] = "systemEventMessage"
        stale_root = _msg("r2", "2026-03-05T00:00:00Z")
        fresh_reply = _msg("a1", "2026-03-05T00:00:00Z")
        live_reply = _msg("a2", "2026-03-05T00:00:00Z")
        store = {
            "a1": StoredMessage(
                id="a1",
                parent_id="r1",
                sender="Ada",
                created="2026-03-05T00:00:00Z",
                last_modified="2026-03-05T00:00:00Z",
                etag="etag-a1",
                edited=False,
                deleted=False,
                content="body of a1",
                attachments=[],
                subject=None,
            )
        }

        converted = convert_chains(
            _ctx(teams_channels_config, local_storage, vault_paths),
            [(system_root, [fresh_reply, live_reply]), (stale_root, [])],
            store,
            MESSAGES_URL,
        )

        assert [m.id for m in converted] == ["a2", "r2"]

    def test_api_base_paths_distinguish_roots_from_replies(
        self, teams_channels_config, local_storage, vault_paths
    ) -> None:
        root = _msg("r1", "2026-03-05T00:00:00Z")
        reply = _msg("a1", "2026-03-05T00:00:00Z")

        with patch.object(ingest, "to_stored_message") as mock_convert:
            convert_chains(_ctx(teams_channels_config, local_storage, vault_paths), [(root, [reply])], {}, MESSAGES_URL)

        bases = [call.args[3] for call in mock_convert.call_args_list]
        assert bases == [f"{MESSAGES_URL}/r1", f"{MESSAGES_URL}/r1/replies/a1"]
