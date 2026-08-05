"""Every extractor's writes are visible to `RecordingStorage`. All eight.

This is the manifest's only real correctness risk. `RecordingStorage` captures
a write by wrapping `StorageBackend`, so an extractor that reached the
filesystem some other way would produce files a hook is never told about --
silently, because nothing else in the system compares the two.

So this test compares the two: run each extractor against recorded Graph
responses over a real `LocalBackend`, then assert the recorded path set equals
the set of files that actually landed. A new write path fails here or nowhere.

Deletions are covered separately: they leave no file behind, so a set equality
cannot see them.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from pytest_httpx import HTTPXMock

from m365_brain.config import (
    CalendarExtractorConfig,
    ContactsExtractorConfig,
    DirectoryExtractorConfig,
    EmailExtractorConfig,
    GraphConfig,
    MailboxConfig,
    OneDriveExtractorConfig,
    SharePointExtractorConfig,
    TeamsChannelsExtractorConfig,
    TeamsChatsExtractorConfig,
)
from m365_brain.m365.client import GraphClient
from m365_brain.m365.extractors import (
    calendar,
    contacts,
    directory,
    email,
    onedrive,
    sharepoint,
    teams_channels,
    teams_chats,
)
from m365_brain.m365.extractors.base import ExtractorContext
from m365_brain.manifest import ChangeRecorder, RecordingStorage
from m365_brain.storage.local import LocalBackend
from m365_brain.vault.removal import RemovalHandler

FIXTURES_DIR = Path(__file__).resolve().parents[3] / "fixtures"

TEAM_ID = "team-1"
CHANNEL_ID = "19:channel-1"


def _fixture(name: str) -> dict:
    return json.loads((FIXTURES_DIR / name).read_text())


def _teams_message(msg_id: str, created: str) -> dict:
    return {
        "id": msg_id,
        "messageType": "message",
        "createdDateTime": created,
        "lastModifiedDateTime": created,
        "etag": "1",
        "lastEditedDateTime": None,
        "deletedDateTime": None,
        "from": {"user": {"displayName": "Alice", "id": "u1"}},
        "body": {"contentType": "text", "content": "hello"},
    }


# name -> (module, config, wire the Graph responses this extractor asks for)
def _email(mock: HTTPXMock) -> None:
    mock.add_response(url=re.compile(r".*/me/mailFolders/Inbox/messages/delta.*"), json=_fixture("email_response.json"))


def _calendar(mock: HTTPXMock) -> None:
    mock.add_response(url=re.compile(r".*/me/calendarView.*"), json=_fixture("calendar_response.json"))


def _contacts(mock: HTTPXMock) -> None:
    mock.add_response(url=re.compile(r".*/me/contacts/delta.*"), json=_fixture("contacts_response.json"))


def _directory(mock: HTTPXMock) -> None:
    mock.add_response(url=re.compile(r".*/users/delta.*"), json=_fixture("directory_response.json"))


def _onedrive(mock: HTTPXMock) -> None:
    mock.add_response(url=re.compile(r".*/me/drive/root/delta.*"), json=_fixture("onedrive_delta_response.json"))


def _sharepoint(mock: HTTPXMock) -> None:
    mock.add_response(url=re.compile(r".*/me/followedSites.*"), json=_fixture("sharepoint_sites_response.json"))
    mock.add_response(url=re.compile(r".*/sites/site-1/drives.*"), json=_fixture("sharepoint_drives_response.json"))
    mock.add_response(url=re.compile(r".*/sites/site-2/drives.*"), json={"value": []})
    mock.add_response(
        url=re.compile(r".*/drives/drive-1/root/delta.*"), json=_fixture("sharepoint_delta_response.json")
    )


def _teams_chats(mock: HTTPXMock) -> None:
    mock.add_response(
        url=re.compile(r".*/me/chats\?.*"),
        json={
            "value": [
                {
                    "id": "19:chat-1",
                    "chatType": "oneOnOne",
                    "topic": None,
                    "members": [{"displayName": "Alice"}, {"displayName": "Bob"}],
                }
            ]
        },
    )
    mock.add_response(
        url=re.compile(r".*/me/chats/.*/messages.*"),
        json={"value": [_teams_message("m1", "2026-06-11T09:00:00Z")]},
    )


def _teams_channels(mock: HTTPXMock) -> None:
    mock.add_response(url=re.compile(r".*/me/joinedTeams.*"), json={"value": [{"id": TEAM_ID, "displayName": "Eng"}]})
    mock.add_response(
        url=re.compile(rf".*/teams/{TEAM_ID}/channels\?.*"),
        json={"value": [{"id": CHANNEL_ID, "displayName": "General"}]},
    )
    mock.add_response(
        url=re.compile(r".*/channels/.*/messages.*"),
        json={"value": [_teams_message("m1", "2026-06-11T09:00:00Z")]},
    )


CASES = {
    "email": (
        email,
        EmailExtractorConfig(
            enabled=True,
            poll_interval_minutes=3,
            mailboxes=[MailboxConfig(address="me", folders=["Inbox"], output_subdir="")],
            lookback_days=30,
            max_items_per_sync=100,
            download_attachments=False,
            max_attachment_size_mb=25,
            attachment_convert_extensions=[],
        ),
        _email,
    ),
    "calendar": (
        calendar,
        CalendarExtractorConfig(enabled=True, poll_interval_minutes=60, lookback_days=30, forward_days=90),
        _calendar,
    ),
    "contacts": (
        contacts,
        ContactsExtractorConfig(
            enabled=True, poll_interval_minutes=1440, max_items_per_sync=500, include_contact_folders=False
        ),
        _contacts,
    ),
    "directory": (
        directory,
        DirectoryExtractorConfig(
            enabled=True,
            poll_interval_minutes=10080,
            include_manager_chain=False,
            include_direct_reports=False,
            only_active_users=True,
        ),
        _directory,
    ),
    "onedrive": (
        onedrive,
        OneDriveExtractorConfig(
            enabled=True,
            poll_interval_minutes=120,
            eager_convert_patterns=[],
            convertible_extensions=[".docx", ".pdf"],
            max_file_size_mb=100,
        ),
        _onedrive,
    ),
    "sharepoint": (
        sharepoint,
        SharePointExtractorConfig(
            enabled=True,
            poll_interval_minutes=240,
            eager_convert_patterns=[],
            convertible_extensions=[".docx", ".pdf"],
            max_file_size_mb=100,
        ),
        _sharepoint,
    ),
    "teams_chats": (
        teams_chats,
        TeamsChatsExtractorConfig(
            enabled=True,
            poll_interval_minutes=5,
            max_messages_per_chat=200,
            download_attachments=False,
            download_inline_images=False,
            max_attachment_size_mb=25,
            attachment_convert_extensions=[],
        ),
        _teams_chats,
    ),
    "teams_channels": (
        teams_channels,
        TeamsChannelsExtractorConfig(
            enabled=True,
            poll_interval_minutes=5,
            max_messages_per_channel=200,
            download_attachments=False,
            download_inline_images=False,
            max_attachment_size_mb=25,
            attachment_convert_extensions=[],
            channels=None,
        ),
        _teams_channels,
    ),
}


@pytest.fixture()
def graph_config() -> GraphConfig:
    return GraphConfig(
        max_retries=1,
        backoff_base_ms=10,
        timeout_seconds=5,
        max_pages=10,
        max_retry_after_seconds=300.0,
        error_message_max_length=200,
    )


def _run(name: str, httpx_mock: HTTPXMock, tmp_path, graph_config, vault_paths):
    """Run one extractor under a recording backend. Returns (recorder, backend)."""
    module, config, wire = CASES[name]
    wire(httpx_mock)

    inner = LocalBackend(str(tmp_path / "vault"))
    recorder = ChangeRecorder()
    storage = RecordingStorage(inner, recorder)
    ctx = ExtractorContext(
        paths=vault_paths,
        converters={},
        removal=RemovalHandler(storage=storage, paths=vault_paths),
        recorder=recorder,
    )
    client = GraphClient(graph_config, lambda: "test-token")
    try:
        module.run(client, storage, {}, config, ctx)
    finally:
        client.close()
    return recorder, inner


def test_every_extractor_is_covered() -> None:
    """The case table is the checklist; a ninth extractor must appear in it."""
    from m365_brain.config import EXTRACTOR_NAMES

    assert set(CASES) == set(EXTRACTOR_NAMES)


@pytest.mark.parametrize("name", sorted(CASES))
def test_recorded_paths_equal_what_landed_on_disk(name, httpx_mock: HTTPXMock, tmp_path, graph_config, vault_paths):
    recorder, inner = _run(name, httpx_mock, tmp_path, graph_config, vault_paths)

    recorded = {change.path for change in recorder.changes()}
    on_disk = set(inner.list_files(""))

    assert on_disk, f"{name} wrote nothing -- the fixture is not exercising it"
    assert recorded == on_disk


@pytest.mark.parametrize("name", sorted(CASES))
def test_nothing_is_recorded_as_removed_on_a_first_run(
    name, httpx_mock: HTTPXMock, tmp_path, graph_config, vault_paths
):
    recorder, _ = _run(name, httpx_mock, tmp_path, graph_config, vault_paths)
    assert [c.path for c in recorder.changes() if c.kind != "added"] == []


@pytest.mark.parametrize("name", ["teams_chats", "teams_channels"])
def test_the_merge_store_extractors_declare_their_record_ids(
    name, httpx_mock: HTTPXMock, tmp_path, graph_config, vault_paths
):
    """The path-level manifest is not a watermark for a file holding N records."""
    recorder, _ = _run(name, httpx_mock, tmp_path, graph_config, vault_paths)
    with_ids = {change.path: change.record_ids for change in recorder.changes() if change.record_ids}
    assert list(with_ids.values()) == [["m1"]]
    assert next(iter(with_ids)).endswith(vault_paths.vault.filenames.conversation)


@pytest.mark.parametrize("name", sorted(set(CASES) - {"teams_chats", "teams_channels"}))
def test_every_other_extractor_declares_no_record_ids(name, httpx_mock: HTTPXMock, tmp_path, graph_config, vault_paths):
    """One file per item means the path already *is* the identity."""
    recorder, _ = _run(name, httpx_mock, tmp_path, graph_config, vault_paths)
    assert all(change.record_ids == [] for change in recorder.changes())


def test_a_removal_through_the_removal_handler_is_recorded(tmp_path, vault_paths):
    """Deletes leave no file, so set equality above cannot see them."""
    inner = LocalBackend(str(tmp_path / "vault"))
    recorder = ChangeRecorder()
    storage = RecordingStorage(inner, recorder)
    removal = RemovalHandler(storage=storage, paths=vault_paths)

    doomed = vault_paths.inbox_item("email", "gone.md")
    inner.write_file(doomed, "bye")
    removal.remove(extractor="email", upstream_id="item-1", path_map={"item-1": doomed})

    assert [(c.path, c.kind) for c in recorder.changes()] == [(doomed, "removed")]
