"""Tests for encrypted token store (SQLite + Fernet)."""

from __future__ import annotations

import sqlite3

import cryptography.fernet
import pytest
from cryptography.fernet import Fernet
from hypothesis import given
from hypothesis import strategies as st

from m365_extract.auth.token_store import TokenStore


@pytest.fixture()
def fernet_key():
    return Fernet.generate_key().decode()


@pytest.fixture()
def store(tmp_path, fernet_key):
    db_path = str(tmp_path / "tokens.db")
    return TokenStore(db_path=db_path, fernet_key=fernet_key)


class TestStoreAndRetrieve:
    def test_round_trip(self, store):
        tokens = {"access_token": "abc123", "refresh_token": "def456", "expires_in": 3600}
        store.store_tokens("user-1", tokens)
        result = store.get_tokens("user-1")
        assert result == tokens

    def test_overwrite_existing(self, store):
        store.store_tokens("user-1", {"access_token": "old"})
        store.store_tokens("user-1", {"access_token": "new"})
        result = store.get_tokens("user-1")
        assert result == {"access_token": "new"}

    def test_get_nonexistent_returns_none(self, store):
        assert store.get_tokens("no-such-user") is None

    def test_delete_tokens(self, store):
        store.store_tokens("user-1", {"access_token": "abc"})
        store.delete_tokens("user-1")
        assert store.get_tokens("user-1") is None

    def test_delete_nonexistent_is_noop(self, store):
        store.delete_tokens("no-such-user")

    def test_list_users(self, store):
        store.store_tokens("alice", {"token": "a"})
        store.store_tokens("bob", {"token": "b"})
        users = store.list_users()
        assert sorted(users) == ["alice", "bob"]

    def test_list_users_empty(self, store):
        assert store.list_users() == []


class TestEncryptionAtRest:
    def test_raw_db_does_not_contain_plaintext_token(self, tmp_path, fernet_key):
        db_path = str(tmp_path / "tokens.db")
        s = TokenStore(db_path=db_path, fernet_key=fernet_key)
        secret_token = "super-secret-access-token-value"
        s.store_tokens("user-1", {"access_token": secret_token})

        raw_bytes = (tmp_path / "tokens.db").read_bytes()
        assert secret_token.encode() not in raw_bytes

    def test_wrong_key_cannot_decrypt(self, tmp_path, fernet_key):
        db_path = str(tmp_path / "tokens.db")
        s1 = TokenStore(db_path=db_path, fernet_key=fernet_key)
        s1.store_tokens("user-1", {"access_token": "secret"})

        wrong_key = Fernet.generate_key().decode()
        s2 = TokenStore(db_path=db_path, fernet_key=wrong_key)
        with pytest.raises(cryptography.fernet.InvalidToken):
            s2.get_tokens("user-1")


class TestWalMode:
    def test_db_uses_wal_journal_mode(self, tmp_path, fernet_key):
        db_path = str(tmp_path / "tokens.db")
        TokenStore(db_path=db_path, fernet_key=fernet_key)
        conn = sqlite3.connect(db_path)
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        conn.close()
        assert mode == "wal"


class TestPropertyBased:
    @given(
        user_id=st.text(min_size=1, max_size=50, alphabet=st.characters(categories=("L", "N", "P"))),
        token_dict=st.dictionaries(
            keys=st.text(min_size=1, max_size=20, alphabet=st.characters(categories=("L",))),
            values=st.text(min_size=0, max_size=100),
            min_size=1,
            max_size=5,
        ),
    )
    def test_any_token_dict_round_trips(self, user_id, token_dict):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            fernet_key = Fernet.generate_key().decode()
            db_path = f"{td}/prop_tokens.db"
            s = TokenStore(db_path=db_path, fernet_key=fernet_key)
            s.store_tokens(user_id, token_dict)
            assert s.get_tokens(user_id) == token_dict
