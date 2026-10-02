"""Tests for Teams chat rendering helpers."""

from __future__ import annotations

from m365_brain.m365.extractors._message_store import StoredMessage
from m365_brain.m365.extractors._teams_chat_writer import chat_title_and_dir, write_chat
from m365_brain.m365.markdown_writer import loads_markdown
from m365_brain.storage.local import LocalBackend
from m365_brain.vault.paths import VaultPaths


class TestChatTitleAndDir:
    def test_uses_topic_when_present(self, vault_paths: VaultPaths) -> None:
        chat = {"topic": "Project Alpha", "members": [], "id": "chat-1"}
        title, _, _ = chat_title_and_dir(chat, vault_paths, "teams_chats")
        assert title == "Project Alpha"

    def test_falls_back_to_participants(self, vault_paths: VaultPaths) -> None:
        chat = {
            "topic": "",
            "members": [{"displayName": "Bob"}, {"displayName": "Alice"}],
            "id": "chat-2",
        }
        title, _, participants = chat_title_and_dir(chat, vault_paths, "teams_chats")
        assert title == "Alice, Bob"
        assert set(participants) == {"Alice", "Bob"}

    def test_falls_back_to_default(self, vault_paths: VaultPaths) -> None:
        chat = {"topic": "", "members": [], "id": "chat-3"}
        title, _, _ = chat_title_and_dir(chat, vault_paths, "teams_chats")
        assert title == "Chat"

    def test_dir_contains_extractor_name(self, vault_paths: VaultPaths) -> None:
        chat = {"topic": "Test", "members": [], "id": "chat-4"}
        _, chat_dir, _ = chat_title_and_dir(chat, vault_paths, "teams_chats")
        assert "teams-chats" in chat_dir


class TestWriteChat:
    def test_writes_markdown_file(self, vault_paths: VaultPaths, local_storage: LocalBackend) -> None:
        chat = {
            "id": "chat-1",
            "topic": "Test Chat",
            "chatType": "group",
            "members": [{"displayName": "Alice"}],
        }
        store = {
            "msg-1": StoredMessage(
                id="msg-1",
                parent_id=None,
                sender="Alice",
                created="2024-01-01T00:00:00Z",
                last_modified="2024-01-01T00:00:00Z",
                etag="e1",
                edited=False,
                deleted=False,
                content="Hello",
                attachments=[],
                subject=None,
            ),
        }

        path = write_chat(local_storage, chat, store, "teams-chats/test", False, vault_paths, "teams_chats")

        assert local_storage.file_exists(path)
        content = local_storage.read_file(path)
        fm, body = loads_markdown(content)
        assert fm["title"] == "Test Chat"
        assert "message_count" in body
