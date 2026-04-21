"""Tests for sync state manager."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from hypothesis import given
from hypothesis import strategies as st

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

    def test_atomic_write_produces_valid_json(self, tmp_path):
        """Verify the atomic write path produces a valid, readable JSON file."""
        state = SyncState(str(tmp_path / "state.json"))
        data = {"delta_link": "https://graph.microsoft.com/v1.0/delta?token=abc", "count": 42}
        state.save("email", data)

        # Read the raw file and verify it's valid JSON
        raw = (tmp_path / "state.json").read_text(encoding="utf-8")
        parsed = json.loads(raw)
        assert parsed["email"] == data

    def test_no_temp_files_left_after_save(self, tmp_path):
        """Atomic write should not leave .tmp files on success."""
        state = SyncState(str(tmp_path / "state.json"))
        state.save("test", {"key": "value"})
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert tmp_files == []

    def test_write_cleanup_on_replace_failure(self, tmp_path):
        """When os.replace fails, the temp file is cleaned up and the error re-raised."""
        state = SyncState(str(tmp_path / "state.json"))
        with (
            patch("m365_extract.state.os.replace", side_effect=OSError("disk full")),
            pytest.raises(OSError, match="disk full"),
        ):
            state.save("email", {"delta": "tok"})
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert tmp_files == []

    def test_write_cleanup_on_write_failure(self, tmp_path):
        """When f.write fails, the temp file is cleaned up and the error re-raised."""
        state = SyncState(str(tmp_path / "state.json"))
        with (
            patch("json.dumps", side_effect=RuntimeError("serialize error")),
            pytest.raises(RuntimeError, match="serialize error"),
        ):
            state.save("email", {"delta": "tok"})
        tmp_files = list(tmp_path.glob("*.tmp"))
        assert tmp_files == []

    @given(
        key=st.text(
            min_size=1,
            max_size=30,
            alphabet=st.characters(whitelist_categories=("L", "N")),
        ),
        values=st.dictionaries(
            st.text(min_size=1, max_size=20, alphabet=st.characters(whitelist_categories=("L", "N"))),
            st.one_of(st.text(max_size=50), st.integers(), st.booleans()),
            max_size=10,
        ),
    )
    def test_save_load_round_trips_property(self, key, values):
        """Property: save(k, v) followed by load(k) always returns v."""
        with tempfile.TemporaryDirectory() as td:
            state = SyncState(str(Path(td) / "state.json"))
            state.save(key, values)
            assert state.load(key) == values
