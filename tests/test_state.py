"""Tests for sync state manager."""

from __future__ import annotations

from m365_extract.state import SyncState


class TestSyncState:
    def test_load_empty_returns_empty_dict(self, tmp_path):
        state = SyncState(str(tmp_path / "state.json"))
        assert state.load("email") == {}

    def test_save_and_load(self, tmp_path):
        state = SyncState(str(tmp_path / "state.json"))
        state.save("email", {"delta_link": "abc123", "last_sync": "2026-03-12T10:00:00Z"})

        loaded = state.load("email")
        assert loaded["delta_link"] == "abc123"
        assert loaded["last_sync"] == "2026-03-12T10:00:00Z"

    def test_multiple_extractors_isolated(self, tmp_path):
        state = SyncState(str(tmp_path / "state.json"))
        state.save("email", {"last_sync": "2026-03-12"})
        state.save("calendar", {"events_count": 42})

        assert state.load("email") == {"last_sync": "2026-03-12"}
        assert state.load("calendar") == {"events_count": 42}

    def test_save_overwrites_existing(self, tmp_path):
        state = SyncState(str(tmp_path / "state.json"))
        state.save("email", {"delta_link": "v1"})
        state.save("email", {"delta_link": "v2"})

        assert state.load("email")["delta_link"] == "v2"

    def test_clear_resets_extractor(self, tmp_path):
        state = SyncState(str(tmp_path / "state.json"))
        state.save("email", {"delta_link": "abc"})
        state.clear("email")

        assert state.load("email") == {}

    def test_clear_nonexistent_key_is_noop(self, tmp_path):
        state = SyncState(str(tmp_path / "state.json"))
        state.clear("nonexistent")
        assert state.load("nonexistent") == {}

    def test_creates_parent_directories(self, tmp_path):
        nested = tmp_path / "deep" / "nested" / "state.json"
        state = SyncState(str(nested))
        state.save("test", {"key": "value"})
        assert state.load("test") == {"key": "value"}
