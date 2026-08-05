"""Tests for TokenServiceAdapter — bridges TokenService to TokenStoreProtocol."""

from __future__ import annotations

import pytest
from cryptography.fernet import Fernet
from sqlmodel import Session, SQLModel, create_engine

from m365_admin.services.token_service import TokenService, TokenServiceAdapter
from m365_brain.m365.auth.token_provider import TokenStoreProtocol
from m365_brain.models import User

pytestmark = pytest.mark.admin


@pytest.fixture()
def fernet_key():
    return Fernet.generate_key().decode()


@pytest.fixture()
def engine():
    eng = create_engine("sqlite://", echo=False)
    SQLModel.metadata.create_all(eng)
    with Session(eng) as session:
        session.add(User(user_id="u-1", display_name="Alice", email="a@b.com", enabled=True))
        session.commit()
    return eng


@pytest.fixture()
def adapter(fernet_key, engine):
    svc = TokenService(fernet_key=fernet_key)
    return TokenServiceAdapter(token_service=svc, engine=engine)


class TestTokenServiceAdapter:
    def test_store_and_get_roundtrip(self, adapter):
        tokens = {"access_token": "at-123", "refresh_token": "rt-456", "expires_at": 9999}
        adapter.store_tokens("u-1", tokens)
        result = adapter.get_tokens("u-1")
        assert result == tokens

    def test_get_nonexistent_returns_none(self, adapter):
        assert adapter.get_tokens("u-1") is None

    def test_satisfies_protocol(self, adapter):
        assert isinstance(adapter, TokenStoreProtocol)
