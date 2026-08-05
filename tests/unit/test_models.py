"""Tests for SQLModel table definitions in m365_brain.models."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, create_engine, select

from m365_brain.models import (
    ExtractorPreference,
    ExtractorStatus,
    TokenRecord,
    User,
    _utcnow,
)


@pytest.fixture()
def session():
    engine = create_engine("sqlite://", echo=False)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _make_user(user_id: str) -> User:
    return User(
        user_id=user_id,
        display_name="Test",
        email=f"{user_id}@example.com",
        enabled=True,
    )


class TestUtcnow:
    def test_returns_utc_datetime(self) -> None:
        result = _utcnow()
        assert isinstance(result, datetime)
        assert result.tzinfo is UTC

    def test_is_recent(self) -> None:
        before = datetime.now(tz=UTC)
        result = _utcnow()
        after = datetime.now(tz=UTC)
        assert before <= result <= after

    @given(st.integers(min_value=0, max_value=100))
    def test_always_utc(self, _n: int) -> None:
        assert _utcnow().tzinfo is UTC


class TestUserModel:
    def test_round_trip(self, session: Session) -> None:
        user = User(
            user_id="u-1",
            display_name="Alice",
            email="alice@example.com",
            enabled=True,
        )
        session.add(user)
        session.commit()

        result = session.get(User, "u-1")
        assert result is not None
        assert result.user_id == "u-1"
        assert result.display_name == "Alice"
        assert result.email == "alice@example.com"
        assert result.enabled is True

    def test_created_at_default_is_recent(self, session: Session) -> None:
        before = datetime.now(tz=UTC)
        session.add(_make_user("u-1"))
        session.commit()
        after = datetime.now(tz=UTC)

        user = session.get(User, "u-1")
        assert user is not None
        # SQLite strips tzinfo on round-trip; compare naive values
        created = user.created_at.replace(tzinfo=None)
        assert (
            before.replace(tzinfo=None) - timedelta(seconds=1)
            <= created
            <= after.replace(tzinfo=None) + timedelta(seconds=1)
        )


class TestTokenRecordModel:
    def test_round_trip(self, session: Session) -> None:
        session.add(_make_user("u-1"))
        session.commit()

        token = TokenRecord(user_id="u-1", encrypted_tokens=b"secret-blob")
        session.add(token)
        session.commit()

        result = session.get(TokenRecord, "u-1")
        assert result is not None
        assert result.encrypted_tokens == b"secret-blob"
        assert isinstance(result.updated_at, datetime)

    def test_foreign_key_defined(self) -> None:
        fk_col = TokenRecord.__table__.c.user_id
        assert len(fk_col.foreign_keys) == 1
        fk = next(iter(fk_col.foreign_keys))
        assert fk.target_fullname == "user.user_id"


class TestExtractorPreferenceModel:
    def test_default_settings_json(self, session: Session) -> None:
        session.add(_make_user("u-1"))
        session.commit()

        pref = ExtractorPreference(
            user_id="u-1",
            extractor_name="email",
            enabled=True,
        )
        session.add(pref)
        session.commit()

        results = session.exec(select(ExtractorPreference).where(ExtractorPreference.user_id == "u-1")).all()
        assert len(results) == 1
        assert results[0].settings_json == "{}"

    def test_custom_settings_json(self, session: Session) -> None:
        session.add(_make_user("u-1"))
        session.commit()

        pref = ExtractorPreference(
            user_id="u-1",
            extractor_name="calendar",
            enabled=False,
            settings_json='{"days": 30}',
        )
        session.add(pref)
        session.commit()

        result = session.exec(select(ExtractorPreference).where(ExtractorPreference.user_id == "u-1")).first()
        assert result is not None
        assert result.settings_json == '{"days": 30}'
        assert result.enabled is False


class TestExtractorStatusModel:
    def test_items_synced_default(self, session: Session) -> None:
        session.add(_make_user("u-1"))
        session.commit()

        status = ExtractorStatus(
            user_id="u-1",
            extractor_name="email",
            status="idle",
        )
        session.add(status)
        session.commit()

        result = session.exec(select(ExtractorStatus).where(ExtractorStatus.user_id == "u-1")).first()
        assert result is not None
        assert result.items_synced == 0

    def test_round_trip_with_all_fields(self, session: Session) -> None:
        session.add(_make_user("u-1"))
        session.commit()

        now = datetime.now(tz=UTC)
        status = ExtractorStatus(
            user_id="u-1",
            extractor_name="calendar",
            status="failed",
            last_run_at=now,
            items_synced=99,
            error_message="timeout",
        )
        session.add(status)
        session.commit()

        result = session.exec(select(ExtractorStatus).where(ExtractorStatus.extractor_name == "calendar")).first()
        assert result is not None
        assert result.status == "failed"
        assert result.items_synced == 99
        assert result.error_message == "timeout"
        # SQLite strips tzinfo; compare naive values
        assert result.last_run_at.replace(tzinfo=None) == now.replace(tzinfo=None)

    def test_unique_constraint(self, session: Session) -> None:
        session.add(_make_user("u-1"))
        session.commit()

        session.add(ExtractorStatus(user_id="u-1", extractor_name="email", status="idle"))
        session.commit()

        session.add(ExtractorStatus(user_id="u-1", extractor_name="email", status="running"))
        with pytest.raises(IntegrityError):
            session.commit()
