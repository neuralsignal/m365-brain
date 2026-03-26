"""Tests for SQLModel table definitions — CRUD operations."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from hypothesis import given
from hypothesis import strategies as st
from sqlmodel import Session, SQLModel, create_engine, select

from m365_extract.models import (
    ExtractorPreference,
    ExtractorStatus,
    TokenRecord,
    User,
)

pytestmark = pytest.mark.admin


@pytest.fixture()
def session():
    engine = create_engine("sqlite://", echo=False)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


class TestUser:
    def test_create_and_read(self, session):
        user = User(user_id="u-1", display_name="Alice", email="alice@example.com", enabled=True)
        session.add(user)
        session.commit()

        result = session.get(User, "u-1")
        assert result is not None
        assert result.display_name == "Alice"
        assert result.email == "alice@example.com"
        assert result.enabled is True
        assert isinstance(result.created_at, datetime)

    def test_update_enabled(self, session):
        session.add(User(user_id="u-1", display_name="Alice", email="a@b.com", enabled=True))
        session.commit()

        user = session.get(User, "u-1")
        user.enabled = False
        session.add(user)
        session.commit()

        refreshed = session.get(User, "u-1")
        assert refreshed.enabled is False

    def test_delete(self, session):
        session.add(User(user_id="u-1", display_name="Alice", email="a@b.com", enabled=True))
        session.commit()

        user = session.get(User, "u-1")
        session.delete(user)
        session.commit()

        assert session.get(User, "u-1") is None

    def test_list_users(self, session):
        session.add(User(user_id="bob", display_name="Bob", email="bob@b.com", enabled=True))
        session.add(User(user_id="alice", display_name="Alice", email="alice@a.com", enabled=True))
        session.commit()

        users = session.exec(select(User).order_by(User.user_id)).all()
        assert [u.user_id for u in users] == ["alice", "bob"]

    @given(
        user_id=st.text(min_size=1, max_size=50, alphabet=st.characters(categories=("L", "N"))),
        display_name=st.text(min_size=1, max_size=100),
        email=st.emails(),
    )
    def test_roundtrip_property(self, user_id, display_name, email):
        engine = create_engine("sqlite://", echo=False)
        SQLModel.metadata.create_all(engine)
        with Session(engine) as s:
            s.add(User(user_id=user_id, display_name=display_name, email=email, enabled=True))
            s.commit()
            result = s.get(User, user_id)
            assert result.user_id == user_id
            assert result.display_name == display_name
            assert result.email == email


class TestTokenRecord:
    def test_create_and_read(self, session):
        session.add(User(user_id="u-1", display_name="A", email="a@b.com", enabled=True))
        session.commit()

        record = TokenRecord(user_id="u-1", encrypted_tokens=b"encrypted-blob")
        session.add(record)
        session.commit()

        result = session.get(TokenRecord, "u-1")
        assert result is not None
        assert result.encrypted_tokens == b"encrypted-blob"


class TestExtractorPreference:
    def test_create_and_query(self, session):
        session.add(User(user_id="u-1", display_name="A", email="a@b.com", enabled=True))
        session.commit()

        pref = ExtractorPreference(user_id="u-1", extractor_name="email", enabled=True, settings_json="{}")
        session.add(pref)
        session.commit()

        results = session.exec(select(ExtractorPreference).where(ExtractorPreference.user_id == "u-1")).all()
        assert len(results) == 1
        assert results[0].extractor_name == "email"
        assert results[0].enabled is True

    def test_multiple_extractors_per_user(self, session):
        session.add(User(user_id="u-1", display_name="A", email="a@b.com", enabled=True))
        session.commit()

        for name in ["email", "calendar", "teams_chats"]:
            session.add(ExtractorPreference(user_id="u-1", extractor_name=name, enabled=True))
        session.commit()

        results = session.exec(select(ExtractorPreference).where(ExtractorPreference.user_id == "u-1")).all()
        assert len(results) == 3


class TestExtractorStatus:
    def test_create_and_read(self, session):
        session.add(User(user_id="u-1", display_name="A", email="a@b.com", enabled=True))
        session.commit()

        now = datetime.now(tz=UTC)
        status = ExtractorStatus(
            user_id="u-1",
            extractor_name="email",
            status="success",
            last_run_at=now,
            items_synced=42,
        )
        session.add(status)
        session.commit()

        results = session.exec(select(ExtractorStatus).where(ExtractorStatus.user_id == "u-1")).all()
        assert len(results) == 1
        assert results[0].status == "success"
        assert results[0].items_synced == 42
        assert results[0].extractor_name == "email"
