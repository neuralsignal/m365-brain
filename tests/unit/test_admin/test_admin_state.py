"""Tests for admin state logic (DB operations, not Reflex state)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func
from sqlmodel import Session, SQLModel, create_engine, select

from m365_brain.models import ExtractorStatus, User

pytestmark = pytest.mark.admin


@pytest.fixture()
def session():
    engine = create_engine("sqlite://", echo=False)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _run_load_users_query(session: Session) -> list[dict]:
    """Execute the same query as AdminState.load_users."""
    latest_run = (
        select(
            ExtractorStatus.user_id,
            func.max(ExtractorStatus.last_run_at).label("max_run_at"),
        )
        .group_by(ExtractorStatus.user_id)
        .subquery()
    )
    stmt = (
        select(User, ExtractorStatus.last_run_at, ExtractorStatus.status)
        .outerjoin(latest_run, User.user_id == latest_run.c.user_id)
        .outerjoin(
            ExtractorStatus,
            (ExtractorStatus.user_id == latest_run.c.user_id)
            & (ExtractorStatus.last_run_at == latest_run.c.max_run_at),
        )
        .order_by(User.user_id)
    )
    rows = session.exec(stmt).all()
    return [
        {
            "user_id": u.user_id,
            "display_name": u.display_name,
            "email": u.email,
            "enabled": u.enabled,
            "created_at": u.created_at.isoformat() if u.created_at else "",
            "last_sync": last_run_at.isoformat() if last_run_at else "Never",
            "last_sync_status": status or "",
        }
        for u, last_run_at, status in rows
    ]


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


class TestLoadUsersQuery:
    """Test the joined query used by AdminState.load_users."""

    def test_users_without_status(self, session):
        session.add(User(user_id="alice", display_name="Alice", email="a@a.com", enabled=True))
        session.add(User(user_id="bob", display_name="Bob", email="b@b.com", enabled=True))
        session.commit()

        result = _run_load_users_query(session)
        assert len(result) == 2
        assert result[0]["user_id"] == "alice"
        assert result[0]["last_sync"] == "Never"
        assert result[0]["last_sync_status"] == ""
        assert result[1]["user_id"] == "bob"

    def test_user_with_latest_status(self, session):
        session.add(User(user_id="u-1", display_name="Alice", email="a@a.com", enabled=True))
        session.commit()

        old = datetime(2026, 1, 1, tzinfo=UTC)
        new = datetime(2026, 6, 1, tzinfo=UTC)
        session.add(ExtractorStatus(user_id="u-1", extractor_name="email", status="failed", last_run_at=old))
        session.add(ExtractorStatus(user_id="u-1", extractor_name="calendar", status="success", last_run_at=new))
        session.commit()

        result = _run_load_users_query(session)
        assert len(result) == 1
        assert result[0]["last_sync"] == new.replace(tzinfo=None).isoformat()
        assert result[0]["last_sync_status"] == "success"

    def test_mixed_users_with_and_without_status(self, session):
        session.add(User(user_id="alice", display_name="Alice", email="a@a.com", enabled=True))
        session.add(User(user_id="bob", display_name="Bob", email="b@b.com", enabled=False))
        session.commit()

        now = datetime.now(tz=UTC)
        session.add(ExtractorStatus(user_id="alice", extractor_name="email", status="success", last_run_at=now))
        session.commit()

        result = _run_load_users_query(session)
        assert len(result) == 2
        alice = next(r for r in result if r["user_id"] == "alice")
        bob = next(r for r in result if r["user_id"] == "bob")
        assert alice["last_sync"] == now.replace(tzinfo=None).isoformat()
        assert alice["last_sync_status"] == "success"
        assert bob["last_sync"] == "Never"
        assert bob["last_sync_status"] == ""

    def test_multiple_users_each_with_statuses(self, session):
        session.add(User(user_id="alice", display_name="Alice", email="a@a.com", enabled=True))
        session.add(User(user_id="bob", display_name="Bob", email="b@b.com", enabled=True))
        session.commit()

        base = datetime(2026, 1, 1, tzinfo=UTC)
        session.add(ExtractorStatus(user_id="alice", extractor_name="email", status="failed", last_run_at=base))
        session.add(
            ExtractorStatus(
                user_id="alice", extractor_name="calendar", status="success", last_run_at=base + timedelta(hours=1)
            )
        )
        session.add(
            ExtractorStatus(
                user_id="bob", extractor_name="email", status="success", last_run_at=base + timedelta(hours=2)
            )
        )
        session.commit()

        result = _run_load_users_query(session)
        assert len(result) == 2
        alice = next(r for r in result if r["user_id"] == "alice")
        bob = next(r for r in result if r["user_id"] == "bob")
        assert alice["last_sync_status"] == "success"
        assert bob["last_sync_status"] == "success"
        assert bob["last_sync"] == (base + timedelta(hours=2)).replace(tzinfo=None).isoformat()
