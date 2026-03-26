"""Tests for admin state logic (DB operations, not Reflex state)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from m365_extract.models import ExtractorStatus, User

pytestmark = pytest.mark.admin


@pytest.fixture()
def session():
    engine = create_engine("sqlite://", echo=False)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


class TestUserManagement:
    def test_list_users_ordered(self, session):
        session.add(User(user_id="bob", display_name="Bob", email="bob@b.com", enabled=True))
        session.add(User(user_id="alice", display_name="Alice", email="alice@a.com", enabled=True))
        session.commit()

        users = session.exec(select(User).order_by(User.user_id)).all()
        assert [u.user_id for u in users] == ["alice", "bob"]

    def test_toggle_user_enabled(self, session):
        session.add(User(user_id="u-1", display_name="Alice", email="a@b.com", enabled=True))
        session.commit()

        user = session.get(User, "u-1")
        user.enabled = not user.enabled
        session.add(user)
        session.commit()

        refreshed = session.get(User, "u-1")
        assert refreshed.enabled is False

    def test_user_with_extractor_status(self, session):
        session.add(User(user_id="u-1", display_name="Alice", email="a@b.com", enabled=True))
        session.commit()

        now = datetime.now(tz=UTC)
        session.add(
            ExtractorStatus(
                user_id="u-1",
                extractor_name="email",
                status="success",
                last_run_at=now,
                items_synced=10,
            )
        )
        session.commit()

        latest = session.exec(
            select(ExtractorStatus).where(ExtractorStatus.user_id == "u-1").order_by(ExtractorStatus.last_run_at.desc())  # type: ignore[union-attr]
        ).first()
        assert latest is not None
        assert latest.status == "success"

    def test_user_without_extractor_status(self, session):
        session.add(User(user_id="u-1", display_name="Alice", email="a@b.com", enabled=True))
        session.commit()

        latest = session.exec(select(ExtractorStatus).where(ExtractorStatus.user_id == "u-1")).first()
        assert latest is None
