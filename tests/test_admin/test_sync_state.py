"""Tests for sync state logic (DB operations, not Reflex state)."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from m365_extract.models import SyncRecord, User

pytestmark = pytest.mark.admin


@pytest.fixture()
def session():
    engine = create_engine("sqlite://", echo=False)
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(User(user_id="u-1", display_name="Alice", email="a@b.com", enabled=True))
        s.commit()
        yield s


class TestSyncRecordQuery:
    def test_latest_sync_for_user(self, session):
        now = datetime.now(tz=UTC)
        session.add(SyncRecord(
            user_id="u-1", started_at=now, status="completed",
            extractors_run=json.dumps(["email"]), items_synced=10,
        ))
        session.commit()

        latest = session.exec(
            select(SyncRecord)
            .where(SyncRecord.user_id == "u-1")
            .order_by(SyncRecord.started_at.desc())
        ).first()
        assert latest is not None
        assert latest.status == "completed"
        assert latest.items_synced == 10

    def test_no_syncs_returns_none(self, session):
        latest = session.exec(
            select(SyncRecord)
            .where(SyncRecord.user_id == "u-1")
            .order_by(SyncRecord.started_at.desc())
        ).first()
        assert latest is None

    def test_multiple_syncs_returns_latest(self, session):
        from datetime import timedelta

        now = datetime.now(tz=UTC)
        old = now - timedelta(hours=1)

        session.add(SyncRecord(
            user_id="u-1", started_at=old, status="completed",
            extractors_run=json.dumps(["email"]), items_synced=5,
        ))
        session.add(SyncRecord(
            user_id="u-1", started_at=now, status="failed",
            extractors_run=json.dumps(["calendar"]), items_synced=0,
            error_message="Connection timeout",
        ))
        session.commit()

        latest = session.exec(
            select(SyncRecord)
            .where(SyncRecord.user_id == "u-1")
            .order_by(SyncRecord.started_at.desc())
        ).first()
        assert latest.status == "failed"
        assert latest.error_message == "Connection timeout"

    def test_history_limit(self, session):
        now = datetime.now(tz=UTC)
        from datetime import timedelta

        for i in range(25):
            session.add(SyncRecord(
                user_id="u-1",
                started_at=now - timedelta(hours=i),
                status="completed",
                items_synced=i,
            ))
        session.commit()

        records = session.exec(
            select(SyncRecord)
            .where(SyncRecord.user_id == "u-1")
            .order_by(SyncRecord.started_at.desc())
            .limit(20)
        ).all()
        assert len(records) == 20

    def test_sync_records_isolated_per_user(self, session):
        session.add(User(user_id="u-2", display_name="Bob", email="b@b.com", enabled=True))
        session.commit()

        now = datetime.now(tz=UTC)
        session.add(SyncRecord(user_id="u-1", started_at=now, status="completed", items_synced=10))
        session.add(SyncRecord(user_id="u-2", started_at=now, status="failed", items_synced=0))
        session.commit()

        u1_records = session.exec(
            select(SyncRecord).where(SyncRecord.user_id == "u-1")
        ).all()
        u2_records = session.exec(
            select(SyncRecord).where(SyncRecord.user_id == "u-2")
        ).all()

        assert len(u1_records) == 1
        assert u1_records[0].status == "completed"
        assert len(u2_records) == 1
        assert u2_records[0].status == "failed"

    def test_extractors_run_json_roundtrip(self, session):
        now = datetime.now(tz=UTC)
        extractors = ["email", "calendar", "teams_chats"]
        session.add(SyncRecord(
            user_id="u-1", started_at=now, status="completed",
            extractors_run=json.dumps(extractors), items_synced=42,
        ))
        session.commit()

        record = session.exec(
            select(SyncRecord).where(SyncRecord.user_id == "u-1")
        ).first()
        assert json.loads(record.extractors_run) == extractors
