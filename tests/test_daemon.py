"""Tests for the daemon sync runner."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import patch

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from m365_extract.daemon import (
    get_enabled_users,
    get_user_extractors,
    run_daemon_cycle,
    sync_user,
    write_sync_record,
)
from m365_extract.models import ExtractorPreference, SyncRecord, User


@pytest.fixture()
def engine():
    eng = create_engine("sqlite://", echo=False)
    SQLModel.metadata.create_all(eng)
    return eng


@pytest.fixture()
def seeded_engine(engine):
    """Engine with two users: one enabled, one disabled."""
    with Session(engine) as session:
        session.add(User(user_id="u-1", display_name="Alice", email="alice@example.com", enabled=True))
        session.add(User(user_id="u-2", display_name="Bob", email="bob@example.com", enabled=False))
        session.add(User(user_id="u-3", display_name="Carol", email="carol@example.com", enabled=True))
        session.commit()
    return engine


class TestGetEnabledUsers:
    def test_returns_only_enabled(self, seeded_engine):
        users = get_enabled_users(seeded_engine)
        user_ids = [u.user_id for u in users]
        assert user_ids == ["u-1", "u-3"]

    def test_empty_table(self, engine):
        users = get_enabled_users(engine)
        assert users == []


class TestGetUserExtractors:
    def test_returns_enabled(self, seeded_engine):
        with Session(seeded_engine) as session:
            session.add(ExtractorPreference(user_id="u-1", extractor_name="email", enabled=True))
            session.add(ExtractorPreference(user_id="u-1", extractor_name="calendar", enabled=False))
            session.add(ExtractorPreference(user_id="u-1", extractor_name="contacts", enabled=True))
            session.commit()

        names = get_user_extractors(seeded_engine, "u-1")
        assert sorted(names) == ["contacts", "email"]

    def test_no_prefs_returns_empty(self, seeded_engine):
        names = get_user_extractors(seeded_engine, "u-1")
        assert names == []


class TestWriteSyncRecord:
    def test_persists(self, seeded_engine):
        now = datetime.now(tz=UTC)
        record = SyncRecord(
            user_id="u-1",
            started_at=now,
            status="running",
            extractors_run=json.dumps(["email"]),
        )
        write_sync_record(seeded_engine, record)

        with Session(seeded_engine) as session:
            results = session.exec(select(SyncRecord).where(SyncRecord.user_id == "u-1")).all()
            assert len(results) == 1
            assert results[0].status == "running"


def _fake_token_provider():
    return "fake-access-token"


class FakeTokenAdapter:
    def get_tokens(self, user_id):
        return {"access_token": "fake", "refresh_token": "fake", "expires_at": 9999999999}

    def store_tokens(self, user_id, tokens):
        pass


class TestSyncUser:
    def test_writes_completed_record(self, seeded_engine, full_config, tmp_path):
        with Session(seeded_engine) as session:
            session.add(ExtractorPreference(user_id="u-1", extractor_name="email", enabled=True))
            session.commit()

        user = get_enabled_users(seeded_engine)[0]

        with (
            patch("m365_extract.daemon.make_web_token_provider", return_value=_fake_token_provider),
            patch("m365_extract.daemon.run_extractors") as mock_run,
        ):
            record = sync_user(full_config, seeded_engine, FakeTokenAdapter(), user, str(tmp_path / "state"))

        assert record.status == "completed"
        assert record.completed_at is not None
        mock_run.assert_called_once()

    def test_writes_failed_record(self, seeded_engine, full_config, tmp_path):
        user = get_enabled_users(seeded_engine)[0]

        with (
            patch("m365_extract.daemon.make_web_token_provider", return_value=_fake_token_provider),
            patch("m365_extract.daemon.run_extractors", side_effect=RuntimeError("Graph API down")),
        ):
            record = sync_user(full_config, seeded_engine, FakeTokenAdapter(), user, str(tmp_path / "state"))

        assert record.status == "failed"
        assert record.completed_at is not None
        assert "Graph API down" in record.error_message


class TestRunDaemonCycle:
    def test_no_users(self, engine, full_config, tmp_path):
        records = run_daemon_cycle(full_config, engine, FakeTokenAdapter(), str(tmp_path / "state"))
        assert records == []

    def test_syncs_enabled_users(self, seeded_engine, full_config, tmp_path):
        with (
            patch("m365_extract.daemon.make_web_token_provider", return_value=_fake_token_provider),
            patch("m365_extract.daemon.run_extractors"),
        ):
            records = run_daemon_cycle(
                full_config, seeded_engine, FakeTokenAdapter(), str(tmp_path / "state")
            )

        assert len(records) == 2
        assert all(r.status == "completed" for r in records)
