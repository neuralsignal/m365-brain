"""Tests for the sync worker."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

from m365_extract.models import ExtractorPreference, ExtractorStatus, User
from m365_extract.worker import (
    get_due_jobs,
    get_enabled_users,
    get_user_extractors,
    run_single_extractor,
    upsert_extractor_status,
)


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
        """No ExtractorPreference rows = nothing enabled, no fallback."""
        names = get_user_extractors(seeded_engine, "u-1")
        assert names == []


class TestUpsertExtractorStatus:
    def test_inserts_new(self, seeded_engine):
        upsert_extractor_status(seeded_engine, "u-1", "email", "running", 0, None)

        with Session(seeded_engine) as session:
            row = session.exec(
                select(ExtractorStatus).where(
                    ExtractorStatus.user_id == "u-1",
                    ExtractorStatus.extractor_name == "email",
                )
            ).first()
            assert row is not None
            assert row.status == "running"
            assert row.items_synced == 0

    def test_updates_existing(self, seeded_engine):
        upsert_extractor_status(seeded_engine, "u-1", "email", "running", 0, None)
        upsert_extractor_status(seeded_engine, "u-1", "email", "success", 42, None)

        with Session(seeded_engine) as session:
            rows = session.exec(
                select(ExtractorStatus).where(
                    ExtractorStatus.user_id == "u-1",
                    ExtractorStatus.extractor_name == "email",
                )
            ).all()
            assert len(rows) == 1
            assert rows[0].status == "success"
            assert rows[0].items_synced == 42

    def test_stores_error(self, seeded_engine):
        upsert_extractor_status(seeded_engine, "u-1", "email", "failed", 0, "Token expired")

        with Session(seeded_engine) as session:
            row = session.exec(select(ExtractorStatus).where(ExtractorStatus.user_id == "u-1")).first()
            assert row.error_message == "Token expired"


class TestGetDueJobs:
    def test_new_user_is_due(self, seeded_engine, full_config):
        """Users with no ExtractorStatus rows are always due."""
        with Session(seeded_engine) as session:
            session.add(ExtractorPreference(user_id="u-1", extractor_name="email", enabled=True))
            session.commit()

        due = get_due_jobs(seeded_engine, full_config)
        pairs = [(u.user_id, ext) for u, ext in due]
        assert ("u-1", "email") in pairs

    def test_recently_synced_not_due(self, seeded_engine, full_config):
        """User with a recent sync is not due yet."""
        with Session(seeded_engine) as session:
            session.add(ExtractorPreference(user_id="u-1", extractor_name="email", enabled=True))
            session.commit()

        upsert_extractor_status(seeded_engine, "u-1", "email", "success", 10, None)

        due = get_due_jobs(seeded_engine, full_config)
        pairs = [(u.user_id, ext) for u, ext in due]
        assert ("u-1", "email") not in pairs

    def test_old_sync_is_due(self, seeded_engine, full_config):
        """User with an old sync is due again."""
        with Session(seeded_engine) as session:
            session.add(ExtractorPreference(user_id="u-1", extractor_name="email", enabled=True))
            session.commit()

        upsert_extractor_status(seeded_engine, "u-1", "email", "success", 10, None)

        with Session(seeded_engine) as session:
            row = session.exec(
                select(ExtractorStatus).where(
                    ExtractorStatus.user_id == "u-1",
                    ExtractorStatus.extractor_name == "email",
                )
            ).first()
            row.last_run_at = datetime.now(tz=UTC) - timedelta(hours=1)
            session.add(row)
            session.commit()

        due = get_due_jobs(seeded_engine, full_config)
        pairs = [(u.user_id, ext) for u, ext in due]
        assert ("u-1", "email") in pairs


def _fake_token_provider():
    return "fake-access-token"


class FakeTokenAdapter:
    def get_tokens(self, user_id):
        return {"access_token": "fake", "refresh_token": "fake", "expires_at": 9999999999}

    def store_tokens(self, user_id, tokens):
        pass


class TestRunSingleExtractor:
    def test_success(self, seeded_engine, full_config, tmp_path):
        user = get_enabled_users(seeded_engine)[0]

        with (
            patch("m365_extract.worker.make_web_token_provider", return_value=_fake_token_provider),
            patch("m365_extract.worker.run_extractors", return_value=42) as mock_run,
            patch("m365_extract.worker.release_advisory_lock"),
        ):
            run_single_extractor(full_config, seeded_engine, FakeTokenAdapter(), user, "email", str(tmp_path))

        mock_run.assert_called_once()

        with Session(seeded_engine) as session:
            row = session.exec(
                select(ExtractorStatus).where(
                    ExtractorStatus.user_id == "u-1",
                    ExtractorStatus.extractor_name == "email",
                )
            ).first()
            assert row.status == "success"
            assert row.items_synced == 42

    def test_failure(self, seeded_engine, full_config, tmp_path):
        from m365_extract.graph_client import GraphApiError

        user = get_enabled_users(seeded_engine)[0]

        with (
            patch("m365_extract.worker.make_web_token_provider", return_value=_fake_token_provider),
            patch("m365_extract.worker.run_extractors", side_effect=GraphApiError("Graph API down")),
            patch("m365_extract.worker.release_advisory_lock"),
        ):
            run_single_extractor(full_config, seeded_engine, FakeTokenAdapter(), user, "email", str(tmp_path))

        with Session(seeded_engine) as session:
            row = session.exec(
                select(ExtractorStatus).where(
                    ExtractorStatus.user_id == "u-1",
                    ExtractorStatus.extractor_name == "email",
                )
            ).first()
            assert row.status == "failed"
            assert "Graph API down" in row.error_message
