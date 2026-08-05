"""Tests for ExtractorStatus DB operations."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from m365_brain.models import ExtractorStatus, User

pytestmark = pytest.mark.admin


@pytest.fixture()
def session():
    engine = create_engine("sqlite://", echo=False)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(User(user_id="u-1", display_name="Alice", email="a@b.com", enabled=True))
        s.commit()
        yield s


class TestExtractorStatusQuery:
    def test_status_for_user(self, session):
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

        row = session.exec(
            select(ExtractorStatus).where(
                ExtractorStatus.user_id == "u-1",
                ExtractorStatus.extractor_name == "email",
            )
        ).first()
        assert row is not None
        assert row.status == "success"
        assert row.items_synced == 10

    def test_no_status_returns_none(self, session):
        row = session.exec(select(ExtractorStatus).where(ExtractorStatus.user_id == "u-1")).first()
        assert row is None

    def test_multiple_extractors(self, session):
        now = datetime.now(tz=UTC)
        session.add(
            ExtractorStatus(user_id="u-1", extractor_name="email", status="success", last_run_at=now, items_synced=10)
        )
        session.add(
            ExtractorStatus(
                user_id="u-1", extractor_name="calendar", status="failed", last_run_at=now, error_message="timeout"
            )
        )
        session.commit()

        rows = session.exec(
            select(ExtractorStatus).where(ExtractorStatus.user_id == "u-1").order_by(ExtractorStatus.extractor_name)
        ).all()
        assert len(rows) == 2
        assert rows[0].extractor_name == "calendar"
        assert rows[0].status == "failed"
        assert rows[1].extractor_name == "email"
        assert rows[1].status == "success"

    def test_status_isolated_per_user(self, session):
        session.add(User(user_id="u-2", display_name="Bob", email="b@b.com", enabled=True))
        session.commit()

        now = datetime.now(tz=UTC)
        session.add(
            ExtractorStatus(user_id="u-1", extractor_name="email", status="success", last_run_at=now, items_synced=10)
        )
        session.add(
            ExtractorStatus(user_id="u-2", extractor_name="email", status="failed", last_run_at=now, items_synced=0)
        )
        session.commit()

        u1_rows = session.exec(select(ExtractorStatus).where(ExtractorStatus.user_id == "u-1")).all()
        u2_rows = session.exec(select(ExtractorStatus).where(ExtractorStatus.user_id == "u-2")).all()

        assert len(u1_rows) == 1
        assert u1_rows[0].status == "success"
        assert len(u2_rows) == 1
        assert u2_rows[0].status == "failed"
