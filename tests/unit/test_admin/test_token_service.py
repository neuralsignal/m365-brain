"""Tests for token encryption service."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from hypothesis import given
from hypothesis import strategies as st
from pydantic import SecretStr
from sqlmodel import Session, SQLModel, create_engine

from m365_admin.services.token_service import TokenService
from m365_brain.models import TokenRecord, User

pytestmark = pytest.mark.admin


@pytest.fixture()
def fernet_key():
    return SecretStr(Fernet.generate_key().decode())


@pytest.fixture()
def session():
    engine = create_engine("sqlite://", echo=False)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(User(user_id="u-1", display_name="Alice", email="a@b.com", enabled=True))
        s.commit()
        yield s


class TestTokenServiceRoundtrip:
    def test_store_and_retrieve(self, session, fernet_key):
        svc = TokenService(fernet_key=fernet_key)
        tokens = {"access_token": "at-xxx", "refresh_token": "rt-yyy", "expires_in": 3600}
        svc.store_tokens(session, user_id="u-1", tokens=tokens)

        result = svc.get_tokens(session, user_id="u-1")
        assert result == tokens

    def test_get_nonexistent_returns_none(self, session, fernet_key):
        svc = TokenService(fernet_key=fernet_key)
        assert svc.get_tokens(session, user_id="u-1") is None

    def test_store_overwrites_existing(self, session, fernet_key):
        svc = TokenService(fernet_key=fernet_key)
        svc.store_tokens(session, user_id="u-1", tokens={"v": 1})
        svc.store_tokens(session, user_id="u-1", tokens={"v": 2})

        result = svc.get_tokens(session, user_id="u-1")
        assert result == {"v": 2}

    def test_delete_tokens(self, session, fernet_key):
        svc = TokenService(fernet_key=fernet_key)
        svc.store_tokens(session, user_id="u-1", tokens={"x": 1})
        svc.delete_tokens(session, user_id="u-1")
        assert svc.get_tokens(session, user_id="u-1") is None

    def test_delete_nonexistent_is_noop(self, session, fernet_key):
        svc = TokenService(fernet_key=fernet_key)
        svc.delete_tokens(session, user_id="u-1")  # should not raise

    def test_list_user_ids(self, session, fernet_key):
        # Add a second user
        session.add(User(user_id="u-2", display_name="Bob", email="b@b.com", enabled=True))
        session.commit()

        svc = TokenService(fernet_key=fernet_key)
        svc.store_tokens(session, user_id="u-1", tokens={"a": 1})
        svc.store_tokens(session, user_id="u-2", tokens={"b": 2})

        ids = svc.list_user_ids(session)
        assert ids == ["u-1", "u-2"]

    def test_encrypted_at_rest(self, session, fernet_key):
        """Verify the raw DB value is not plaintext JSON."""
        svc = TokenService(fernet_key=fernet_key)
        svc.store_tokens(session, user_id="u-1", tokens={"secret": "value"})

        raw = session.get(TokenRecord, "u-1")
        assert b'"secret"' not in raw.encrypted_tokens

    @given(
        access_token=st.text(min_size=1, max_size=200),
        refresh_token=st.text(min_size=1, max_size=200),
        expires_in=st.integers(min_value=1, max_value=86400),
    )
    def test_roundtrip_property(self, access_token, refresh_token, expires_in):
        key = SecretStr(Fernet.generate_key().decode())
        engine = create_engine("sqlite://", echo=False)
        SQLModel.metadata.create_all(engine)
        with Session(engine) as s:
            s.add(User(user_id="u-p", display_name="P", email="p@b.com", enabled=True))
            s.commit()

            svc = TokenService(fernet_key=key)
            tokens = {"access_token": access_token, "refresh_token": refresh_token, "expires_in": expires_in}
            svc.store_tokens(s, user_id="u-p", tokens=tokens)
            result = svc.get_tokens(s, user_id="u-p")
            assert result == tokens
