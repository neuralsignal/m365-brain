"""Tests for user manager (CRUD for multi-user sync)."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from m365_extract.user_manager import UserManager, UserRecord


@pytest.fixture()
def manager(tmp_path):
    db_path = str(tmp_path / "users.db")
    return UserManager(db_path=db_path)


class TestCreateAndGet:
    def test_create_user(self, manager):
        user = manager.create_user(
            user_id="user-1",
            display_name="Alice Smith",
            email="alice@example.com",
        )
        assert isinstance(user, UserRecord)
        assert user.user_id == "user-1"
        assert user.display_name == "Alice Smith"
        assert user.email == "alice@example.com"
        assert user.enabled is True

    def test_get_user(self, manager):
        manager.create_user(user_id="user-1", display_name="Alice", email="alice@example.com")
        user = manager.get_user("user-1")
        assert user is not None
        assert user.display_name == "Alice"

    def test_get_nonexistent_returns_none(self, manager):
        assert manager.get_user("no-such-user") is None

    def test_duplicate_user_id_raises(self, manager):
        manager.create_user(user_id="user-1", display_name="Alice", email="alice@example.com")
        with pytest.raises(ValueError, match="already exists"):
            manager.create_user(user_id="user-1", display_name="Bob", email="bob@example.com")


class TestUpdate:
    def test_enable_disable(self, manager):
        manager.create_user(user_id="user-1", display_name="Alice", email="alice@example.com")
        manager.set_enabled("user-1", enabled=False)
        user = manager.get_user("user-1")
        assert user.enabled is False

        manager.set_enabled("user-1", enabled=True)
        user = manager.get_user("user-1")
        assert user.enabled is True

    def test_enable_nonexistent_raises(self, manager):
        with pytest.raises(ValueError, match="not found"):
            manager.set_enabled("no-such-user", enabled=True)


class TestDelete:
    def test_delete_user(self, manager):
        manager.create_user(user_id="user-1", display_name="Alice", email="alice@example.com")
        manager.delete_user("user-1")
        assert manager.get_user("user-1") is None

    def test_delete_nonexistent_is_noop(self, manager):
        manager.delete_user("no-such-user")


class TestList:
    def test_list_users(self, manager):
        manager.create_user(user_id="alice", display_name="Alice", email="alice@example.com")
        manager.create_user(user_id="bob", display_name="Bob", email="bob@example.com")
        users = manager.list_users()
        assert len(users) == 2
        ids = [u.user_id for u in users]
        assert sorted(ids) == ["alice", "bob"]

    def test_list_users_empty(self, manager):
        assert manager.list_users() == []


class TestExtractorPreferences:
    def test_set_and_get_preferences(self, manager):
        manager.create_user(user_id="user-1", display_name="Alice", email="alice@example.com")
        prefs = {"email": True, "calendar": True, "teams_chats": False}
        manager.set_extractor_preferences("user-1", prefs)
        result = manager.get_extractor_preferences("user-1")
        assert result == prefs

    def test_default_preferences_is_empty_dict(self, manager):
        manager.create_user(user_id="user-1", display_name="Alice", email="alice@example.com")
        assert manager.get_extractor_preferences("user-1") == {}

    def test_preferences_nonexistent_user_raises(self, manager):
        with pytest.raises(ValueError, match="not found"):
            manager.set_extractor_preferences("no-such-user", {"email": True})


class TestWalMode:
    def test_db_uses_wal_journal_mode(self, tmp_path):
        import sqlite3

        db_path = str(tmp_path / "users.db")
        UserManager(db_path=db_path)
        conn = sqlite3.connect(db_path)
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        conn.close()
        assert mode == "wal"


class TestPropertyBased:
    @given(
        user_id=st.text(min_size=1, max_size=50, alphabet=st.characters(categories=("L", "N"))),
        display_name=st.text(min_size=1, max_size=100),
        email=st.emails(),
    )
    def test_any_user_round_trips(self, user_id, display_name, email):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            db_path = f"{td}/prop_users.db"
            m = UserManager(db_path=db_path)
            user = m.create_user(user_id=user_id, display_name=display_name, email=email)
            retrieved = m.get_user(user_id)
            assert retrieved.user_id == user.user_id
            assert retrieved.display_name == user.display_name
            assert retrieved.email == user.email
